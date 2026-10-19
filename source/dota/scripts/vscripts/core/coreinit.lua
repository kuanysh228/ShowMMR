--	ShowMMR dashboard mod by AveYo	--

if not IsServer() then return end -- local lua server instance only

ShowMMR = ShowMMR or {}

-- history is kept in archived convars (written to Steam/userdata/<id>/570/local/cfg/machine_convars.vcfg), which are
-- readable again after a restart, unlike the binds file that LoadKeyValues cannot reach since the USRLOCAL change
local VARS = 64     -- showmmr_<user>_1 - showmmr_<user>_64
local PER_VAR = 8   -- matches per convar, ~20 chars each, so up to 512 recent matches are kept

local function log(...)
	print('[ShowMMR]', ...)
end

-- "time:[mmr,change],time:[mmr,change]" -> into[time] = {mmr, change}
-- console output piped into a command gets split around ':' so whitespace is allowed between the parts
local function parse_entries(text, into)
	local count = 0
	for t, mmr, change in tostring(text):gmatch('(%d+)%s*:%s*%[%s*(%-?%d+)%s*,%s*(%-?%d+)%s*%]') do
		into[tonumber(t)] = {tonumber(mmr), tonumber(change)}
		count = count + 1
	end
	return count
end

local function merge(into, from)
	for k, v in pairs(from) do into[k] = v end
end

local function sorted_times(t)
	local list = {}
	for k in pairs(t) do table.insert(list, k) end
	table.sort(list, function(a, b) return a > b end)
	return list
end

function ShowMMR:Init(e)
	if GameRules then return end -- dashboard only

	-- support multiple users data on the same machine - 0 is any last user
	self.user = tostring(e.networkid or ''):match('^%[%a:[0-5]:(%d+).*%]$') or '0'
	self.data = {}; self.history = {}

	-- the vm is restarted after every game, so the convars are registered again - and keep their saved values
	local saved = 0
	for n = 1, VARS do
		local name = self:VarName(n)
		Convars:RegisterConvar(name, '', 'ShowMMR history', FCVAR_ARCHIVE)
		saved = saved + parse_entries(Convars:GetStr(name), self.data)
	end
	log('convars', saved .. ' matches')

	-- history exported by the ShowMMR tool to game/dota/cfg/showmmr_<user>.vcfg, only fills in what is not saved yet
	local imported = 0
	local tool = LoadKeyValues('cfg/showmmr_' .. self.user .. '.vcfg')
	if tool ~= nil and tool.matches ~= nil then
		for _, v in pairs(tool.matches) do
			local t = tonumber(v.date)
			if t and self.data[t] == nil then
				self.data[t] = {tonumber(v.mmr), tonumber(v.outcome)}
				imported = imported + 1
			end
		end
		log('cfg/showmmr_' .. self.user .. '.vcfg', imported .. ' matches imported')
	end
	merge(self.history, self.data)
	if imported > 0 then self:Persist() end

	-- cfg initialization - once per player_connect event - note that lua vm is restarted after every game
	if self.cfg == nil then
		self.cfg = {}

		-- console output piped into this command lands in self.cfg [recent_game_time_1=timestamp].. or self.data
		Convars:RegisterCommand('cfg', function(_, ...)
			local args = {...}
			local key, val = args[1], args[#args]
			local line = table.concat(args, ' ')
			log('cfg', line:sub(1, 120))
			if parse_entries(line, self.data) > 0 then return end
			if key and key:match('^%l[%l%d_]+$') and val then
				self.cfg[key] = val
				if key == 'cfg_updated' and val == '1' then
					self:Event(key) -- the echo comes last, so everything before it is in self.cfg by now
				end
			end
		end, 'pipe console keyvalue-like output to vscript cfg [recent_game_time_1=timestamp]..', 0)

		if CustomGameEventManager then
			CustomGameEventManager:RegisterListener('ShowMMR_Refresh', function(...) return ShowMMR:Refresh(...) end)
		else
			log('CustomGameEventManager is missing')
		end
	end

	self:Publish()
end

function ShowMMR:VarName(n)
	return 'showmmr_' .. self.user .. '_' .. n
end

-- newest matches first, PER_VAR to a convar, then flush the archived convars to machine_convars.vcfg
function ShowMMR:Persist()
	local ordered = sorted_times(self.data)
	for n = 1, VARS do
		local parts = {}
		for i = (n - 1) * PER_VAR + 1, math.min(n * PER_VAR, #ordered) do
			local t = ordered[i]
			table.insert(parts, t .. ':[' .. self.data[t][1] .. ',' .. self.data[t][2] .. ']')
		end
		Convars:SetStr(self:VarName(n), table.concat(parts, ','))
	end
	SendToServerConsole('host_writeconfig;')
	log('saved', math.min(#ordered, VARS * PER_VAR) .. ' matches')
end

-- make the local vscript mmr data available to panorama ui via CustomNetTables
function ShowMMR:Publish()
	local kv, i, j, limit = {}, 1, 1, 500
	for k, v in pairs(self.history) do
		kv[' ' .. k] = {v[1], v[2]}  -- must save k as a string due to CustomNetTables rounding bug
		i = i + 1  -- if there are 500 + matches do multiple runs to stay under 16384 bytes as per valve wiki
		if i > limit then
			CustomNetTables:SetTableValue('ShowMMR_history', 'kv' .. j, kv)
			j = j + 1; i = 1; kv = {}
		end
	end
	CustomNetTables:SetTableValue('ShowMMR_history', 'kv', kv)
	local count = 0
	for _ in pairs(self.history) do count = count + 1 end
	log('published', count .. ' matches')
end

function ShowMMR:Refresh(_, e)
	-- e event received from panorama scripts/showmmr.js: current mmr and the start time of the latest ranked match
	self.mmr = tonumber(e.mmr) or -1
	self.time = tonumber(e.time) or 0
	log('refresh', self.mmr, self.time)
	if self.time > 0 then return self:Save() end

	-- the match list was not loaded, grab recent_game_time.. values from console into self.cfg table instead
	SendToServerConsole('dota_game_account_client_debug | cfg; echo "cfg_updated: 1" | cfg;')
end

function ShowMMR:Event(name)
	if name == 'cfg_updated' then
		self:Save()
	end
end

function ShowMMR:Save()
	-- latest ranked match from panorama, or the latest match of any kind from the console
	local latest = self.time or 0
	if latest <= 0 then latest = tonumber(self.cfg.recent_game_time_1) or 0 end
	local rank = self.mmr or 0
	local serialize = false
	log('save', latest, rank)

	local function store(t, mmr, change)
		self.data[t] = {mmr, change}
		self.history[t] = {mmr, change}
		CustomNetTables:SetTableValue('ShowMMR_update', ' ' .. t, {mmr, change})
		log('match', t, mmr, change)
		serialize = true
	end

	-- newest match before t with a known mmr
	local function previous(t)
		local found
		for k, v in pairs(self.history) do
			if k < t and v[1] > 0 and (found == nil or k > found) then found = k end
		end
		return found and self.history[found] or nil
	end

	if latest > 0 and rank > 0 then
		local known, before = self.history[latest], previous(latest)
		if known == nil or known[1] == 0 or (known[1] == rank and known[2] == 0 and before ~= nil) then
			store(latest, rank, before and rank - before[1] or 0)
		elseif known[1] ~= rank then
			-- the number changed after a match that is not in the list yet, a later refresh will catch it
			log('mmr', rank, 'is newer than match', latest, known[1])
		end
	end

	-- discovered recent_game_time_x can be unreliable causing the (0) changes
	local ordered = sorted_times(self.data)
	local fixup = -1
	for _, t in ipairs(ordered) do
		local mmr, change = self.data[t][1], self.data[t][2]
		if fixup == -1 and mmr ~= 0 and change == 0 then fixup = t end
		if fixup ~= -1 and t ~= fixup and mmr ~= 0 and mmr ~= self.data[fixup][1] then
			store(fixup, self.data[fixup][1], self.data[fixup][1] - mmr)
			fixup = -1
		end
	end

	if serialize == true then self:Persist() end
end

ListenToGameEvent('player_connect', function(e) ShowMMR:Init(e) end, nil)
log('loaded')
