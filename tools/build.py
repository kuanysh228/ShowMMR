#!/usr/bin/env python3
"""ShowMMR mod builder.

Compiles the panorama sources from source/dota and packs them into a VPK,
using only the Python standard library - Dota 2 Workshop Tools are not needed.

  python3 tools/build.py                 build release/pak02_dir.vpk
  python3 tools/build.py --install       build, then copy it into the mounted language folder
  python3 tools/build.py --uninstall     remove it from the game folder
"""

import argparse
import hashlib
import os
import re
import shutil
import struct
import sys
import uuid
import zlib
import xml.parsers.expat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'source', 'dota')
RELEASE = os.path.join(ROOT, 'release')
VPK_NAME = 'pak02_dir.vpk'


# --------------------------------------------------------------------------------------------------
# KV3 binary writer (version 4, uncompressed) - mirrors ValveResourceFormat's BinaryKV3 serializer

KV3_MAGIC4 = 0x4B563304
KV3_FORMAT_GENERIC = uuid.UUID('7412167c-06e9-4698-aff2-e63eb59037e7').bytes_le

T_NULL, T_STRING, T_ARRAY, T_OBJECT = 1, 6, 8, 9
T_INT64, T_INT32, T_UINT32 = 3, 11, 12
T_TRUE, T_FALSE, T_INT64_ZERO, T_INT64_ONE = 13, 14, 15, 16
FLAG_RESOURCE_NAME = 2


class I32(int):
    pass


class U32(int):
    pass


class ResourceName(str):
    """String flagged as resource_name:"..." in KV3."""


class KV3Writer:
    def __init__(self):
        self.strings = {}
        self.bytes4 = bytearray()
        self.bytes8 = bytearray()
        self.types = bytearray()
        self.count_objects = 0
        self.count_arrays = 0

    def string_id(self, s):
        if not s:
            return -1
        return self.strings.setdefault(s, len(self.strings))

    def write_type(self, t, flag=0):
        if flag:
            self.types += bytes((t | 0x80, flag))
        else:
            self.types.append(t)

    def write_value(self, v):
        if v is None:
            self.write_type(T_NULL)
        elif isinstance(v, bool):
            self.write_type(T_TRUE if v else T_FALSE)
        elif isinstance(v, (I32, U32)) and v != 0:
            self.write_type(T_INT32 if isinstance(v, I32) else T_UINT32)
            self.bytes4 += struct.pack('<i' if isinstance(v, I32) else '<I', v)
        elif isinstance(v, int):
            if v == 0:
                self.write_type(T_INT64_ZERO)
            elif v == 1:
                self.write_type(T_INT64_ONE)
            else:
                self.write_type(T_INT64)
                self.bytes8 += struct.pack('<q', v)
        elif isinstance(v, str):
            self.write_type(T_STRING, FLAG_RESOURCE_NAME if isinstance(v, ResourceName) else 0)
            self.bytes4 += struct.pack('<i', self.string_id(v))
        elif isinstance(v, list):
            self.write_type(T_ARRAY)
            self.count_arrays += 1
            self.bytes4 += struct.pack('<i', len(v))
            for item in v:
                self.write_value(item)
        elif isinstance(v, dict):
            self.write_type(T_OBJECT)
            self.count_objects += 1
            self.bytes4 += struct.pack('<i', len(v))
            for key, item in v.items():
                self.bytes4 += struct.pack('<i', self.string_id(key))
                self.write_value(item)
        else:
            raise TypeError(f'unsupported KV3 value: {v!r}')

    def serialize(self, root):
        self.write_value(root)
        bytes4 = struct.pack('<i', len(self.strings)) + self.bytes4
        buf = bytearray(bytes4)
        buf += b'\0' * (-len(buf) % 8)  # versions before 5 align the 8-byte lane even when it is empty
        buf += self.bytes8
        strings_start = len(buf)
        for s in self.strings:
            buf += s.encode('utf-8') + b'\0'
        buf += self.types
        count_types = len(buf) - strings_start
        buf += struct.pack('<I', 0xFFEEDD00)  # no binary blobs

        header = struct.pack('<i', KV3_MAGIC4) + KV3_FORMAT_GENERIC
        header += struct.pack('<IHH', 0, 0, 0)  # uncompressed, no dictionary, no frame size
        header += struct.pack('<iiii', 0, len(bytes4) // 4, len(self.bytes8) // 8, count_types)
        header += struct.pack('<HH', self.count_objects, self.count_arrays)
        header += struct.pack('<ii', len(buf), len(buf))
        header += struct.pack('<iiii', 0, 0, 0, 0)  # blob count, blob bytes, 2-byte values, block sizes
        return header + bytes(buf)


def kv3(root):
    return KV3Writer().serialize(root)


# --------------------------------------------------------------------------------------------------
# Source 2 resource container

def resource(version, blocks):
    table_end = 16 + 12 * len(blocks)
    positions, pos = [], table_end
    for _, data in blocks:
        pos += -pos % 16
        positions.append(pos)
        pos += len(data)

    out = bytearray(struct.pack('<IHHII', pos, 12, version, 8, len(blocks)))
    for i, (name, data) in enumerate(blocks):
        offset_field = 16 + 12 * i + 4
        out += name.encode() + struct.pack('<II', positions[i] - offset_field, len(data))
    for (_, data), start in zip(blocks, positions):
        out += b'\0' * (start - len(out))
        out += data
    return bytes(out)


def edit_info(source_name, compiled_name, crc, compiler, compiler_id, fingerprint, weak_refs=None):
    """RED2 block: the edit info Valve's resourcecompiler stores next to each compiled file."""
    dependency = lambda name, crc, exists: {
        'm_RelativeFilename': name,
        'm_SearchPath': 'dota',
        'm_nFileCRC': U32(crc),
        'm_bOptional': not exists,
        'm_bFileExists': exists,
        'm_bIsGameFile': False,
    }
    inputs = [dependency(source_name, crc, True), dependency(compiled_name, 0, False)]
    if weak_refs is not None:
        inputs.reverse()  # layouts list the optional compiled name first
    info = {
        'm_InputDependencies': inputs,
        'm_AdditionalInputDependencies': [],
        'm_ArgumentDependencies': [{
            'm_ParameterName': '___OverrideInputData___',
            'm_ParameterType': 'BinaryBlobArg',
            'm_nFingerprint': 0,
            'm_nFingerprintDefault': 0,
        }],
        'm_SpecialDependencies': [
            {'m_String': compiler, 'm_CompilerIdentifier': compiler_id, 'm_nFingerprint': U32(fingerprint), 'm_nUserData': 0},
            {'m_String': 'Panorama Preprocessor Presence/Version', 'm_CompilerIdentifier': compiler_id, 'm_nFingerprint': 0, 'm_nUserData': 0},
        ],
    }
    if weak_refs is not None:
        info['m_SpecialInputDependencies'] = []
    info['m_AdditionalRelatedFiles'] = []
    info['m_ChildResourceList'] = []
    info['m_WeakReferenceList'] = [ResourceName(r) for r in weak_refs or []]
    info['m_SearchableUserData'] = {'IsChildResource': 0}
    info['m_SubassetReferences'] = None
    info['m_SubassetDefinitions'] = None
    return kv3(info)


# --------------------------------------------------------------------------------------------------
# Panorama compilers

def compile_script(rel_path, text):
    """panorama/scripts/*.js -> .vjs_c (PanoramaScript v4: RED2 + raw source in DATA)."""
    data = text.encode('utf-8')
    base = rel_path[:-len('.js')]
    red2 = edit_info(rel_path, base + '.vjs', zlib.crc32(data), 'JavaScript Compiler Version', 'CompileJavaScript', 4)
    return base + '.vjs_c', resource(4, [('RED2', red2), ('DATA', data)])


def ast(etype, name=None, children=(), pos=None):
    node = {'eType': etype}
    if name is not None:
        node['name'] = name
    if len(children) == 1:
        node['child'] = children[0]
    elif children:
        node['vecChildren'] = list(children)
    if pos is not None:
        node['sourceLineColumn'] = [I32(x) for x in pos]
    return node


def reference_name(src):
    name = src.split('://', 1)[-1]
    return name[:-2] if name.endswith('_c') else name


def parse_xml(text):
    stack = [{'tag': None, 'attrs': [], 'children': []}]
    parser = xml.parsers.expat.ParserCreate()
    parser.ordered_attributes = True

    def start(tag, attrs):
        # expat reports the column of '<' (0-based), Valve stores the 1-based column of the tag name
        node = {'tag': tag, 'attrs': list(zip(attrs[::2], attrs[1::2])), 'children': [],
                'pos': (parser.CurrentLineNumber, parser.CurrentColumnNumber + 2)}
        stack[-1]['children'].append(node)
        stack.append(node)

    parser.StartElementHandler = start
    parser.EndElementHandler = lambda tag: stack.pop()
    parser.Parse(text, True)
    return stack[0]['children'][0]


def compile_layout(rel_path, text):
    """panorama/layout/*.xml -> .vxml_c (PanoramaLayout v3: RED2 + CRC in DATA + AST in LaCo)."""
    root = parse_xml(text)
    if root['tag'] != 'root':
        raise ValueError(f'{rel_path}: top-level element must be <root>')
    refs = set()

    def include_list(node, etype):
        includes = []
        for inc in node['children']:
            src = dict(inc['attrs']).get('src')
            if inc['tag'] != 'include' or not src:
                raise ValueError(f'{rel_path}: only <include src="..."> is supported inside <{node["tag"]}>')
            refs.add(reference_name(src))
            includes.append(ast('INCLUDE', children=[ast('REFERENCE_COMPILED', reference_name(src))], pos=inc['pos']))
        return ast(etype, children=includes, pos=node['pos'])

    def panel(node):
        if node['tag'] in ('styles', 'scripts', 'script', 'snippets', 'include'):
            raise ValueError(f'{rel_path}: <{node["tag"]}> is not supported here')
        children = [ast('PANEL_ATTRIBUTE', k, [ast('PANEL_ATTRIBUTE_VALUE', v)]) for k, v in node['attrs']]
        children += [panel(child) for child in node['children']]
        return ast('PANEL', node['tag'], children, node['pos'])

    top = []
    for node in root['children']:
        if node['tag'] == 'styles':
            top.append(include_list(node, 'STYLES'))
        elif node['tag'] == 'scripts':
            top.append(include_list(node, 'SCRIPTS'))
        else:
            top.append(panel(node))

    data = text.encode('utf-8')
    crc = zlib.crc32(data)
    base = rel_path[:-len('.xml')]
    red2 = edit_info(rel_path, base + '.vxml', crc, 'Panorama Layout Compiler Version', 'CompilePanorama', 16, sorted(refs))
    laco = kv3({'m_AST': {'m_pRoot': ast('ROOT', children=top, pos=())}})
    return base + '.vxml_c', resource(3, [('RED2', red2), ('DATA', struct.pack('<IH', crc, 0)), ('LaCo', laco)])


# --------------------------------------------------------------------------------------------------
# VPK version 2, everything stored in the _dir file

def write_vpk(files, out_path):
    tree = {}
    for path in sorted(files):
        directory, filename = os.path.split(path)
        name, ext = os.path.splitext(filename)
        tree.setdefault(ext[1:] or ' ', {}).setdefault(directory or ' ', []).append((name, path))

    tree_bytes, data = bytearray(), bytearray()
    for ext, dirs in tree.items():
        tree_bytes += ext.encode() + b'\0'
        for directory, entries in dirs.items():
            tree_bytes += directory.encode() + b'\0'
            for name, path in entries:
                content = files[path]
                tree_bytes += name.encode() + b'\0'
                tree_bytes += struct.pack('<IHHIIH', zlib.crc32(content), 0, 0x7FFF, len(data), len(content), 0xFFFF)
                data += content
            tree_bytes += b'\0'
        tree_bytes += b'\0'
    tree_bytes += b'\0'

    header = struct.pack('<IIIIIII', 0x55AA1234, 2, len(tree_bytes), len(data), 0, 48, 0)
    tree_md5 = hashlib.md5(tree_bytes).digest()
    archive_md5 = hashlib.md5(b'').digest()
    whole_md5 = hashlib.md5(header + tree_bytes + data + tree_md5 + archive_md5).digest()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(header + tree_bytes + data + tree_md5 + archive_md5 + whole_md5)


# --------------------------------------------------------------------------------------------------
# Game folder detection

def steam_roots():
    roots = []
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                roots.append(winreg.QueryValueEx(key, 'SteamPath')[0])
        except OSError:
            pass
        roots.append(r'C:\Program Files (x86)\Steam')
    else:
        home = os.path.expanduser('~')
        roots += [os.path.join(home, '.local/share/Steam'), os.path.join(home, '.steam/steam'),
                  os.path.join(home, '.var/app/com.valvesoftware.Steam/.local/share/Steam')]
    return [r for r in roots if os.path.isdir(r)]


def find_dota():
    for root in steam_roots():
        libraries = [root]
        vdf = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            with open(vdf, encoding='utf-8', errors='replace') as f:
                libraries += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
        for library in libraries:
            game = os.path.join(library, 'steamapps', 'common', 'dota 2 beta', 'game')
            if os.path.isfile(os.path.join(game, 'dota', 'gameinfo.gi')):
                return game
    return None


def audio_language(game):
    try:
        with open(os.path.join(game, 'dota', 'cfg', 'boot.vcfg'), encoding='utf-8') as f:
            match = re.search(r'"AudioLanguage"\s+"([^"]+)"', f.read())
            return match.group(1).lower() if match else None
    except OSError:
        return None


# --------------------------------------------------------------------------------------------------

def build():
    files = {}
    for dirpath, _, filenames in os.walk(SOURCE):
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, SOURCE).replace(os.sep, '/')
            with open(full, 'rb') as f:
                content = f.read()
            if rel.startswith('panorama/layout/') and rel.endswith('.xml'):
                rel, content = compile_layout(rel, content.decode('utf-8'))
            elif rel.startswith('panorama/scripts/') and rel.endswith('.js'):
                rel, content = compile_script(rel, content.decode('utf-8'))
            elif rel.startswith('panorama/'):
                raise ValueError(f'{rel}: no compiler for this panorama file')
            files[rel] = content
            print(f'  {rel}  ({len(content)} bytes)')

    out = os.path.join(RELEASE, VPK_NAME)
    write_vpk(files, out)
    print(f'Built {os.path.relpath(out, ROOT)}')
    return out


def main():
    parser = argparse.ArgumentParser(description='Build the ShowMMR mod VPK')
    parser.add_argument('--install', action='store_true', help='copy the VPK into the game folder after building')
    parser.add_argument('--uninstall', action='store_true', help='remove the VPK from the game folder')
    parser.add_argument('--game', help='path to "dota 2 beta/game" if it is not detected')
    parser.add_argument('--language', help='language folder to use instead of AudioLanguage from boot.vcfg')
    args = parser.parse_args()

    if not args.uninstall:
        vpk = build()
    if not (args.install or args.uninstall):
        return

    game = args.game or find_dota()
    if not game:
        sys.exit('Dota 2 not found, pass --game "<library>/steamapps/common/dota 2 beta/game"')
    language = args.language or audio_language(game)
    if not language or language == 'english':
        sys.exit('English audio has no language folder for mods: switch the audio language '
                 '(Settings > Audio, e.g. to russian) or pass --language')
    target = os.path.join(game, 'dota_' + language)
    if args.uninstall:
        if os.path.isfile(os.path.join(target, VPK_NAME)):
            os.remove(os.path.join(target, VPK_NAME))
            print(f'Removed {os.path.join(target, VPK_NAME)}')
        return
    if not os.path.isdir(target):
        sys.exit(f'{target} does not exist - is AudioLanguage "{language}" installed?')
    shutil.copy2(vpk, os.path.join(target, VPK_NAME))
    print(f'Installed to {os.path.join(target, VPK_NAME)}')


if __name__ == '__main__':
    main()
