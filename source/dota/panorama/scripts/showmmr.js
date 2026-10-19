'use strict';

/// ShowMMR dashboard mod by AveYo - included from base.xml, so this context lives as long as the dashboard
/// Valve's own layouts are no longer replaced: rows on the stats page and the last match background are patched
/// from here, which keeps the mod from breaking the profile page whenever its layout changes

(function() {
	const UI_STATE_LOADING_SCREEN = 1, UI_STATE_DASHBOARD = 3; /// DOTA_GameUIState_t

	const state = {
		history: {},       /// match start time -> [mmr, change]
		rows: {},          /// recent games row stamp -> match start time
		ui: UI_STATE_DASHBOARD,
		refreshing: false,
		again: false,
		timer: null,
		refreshed: false,
		sent: -1,          /// last mmr forwarded to vscript
		sentTime: 0,       /// start time of the latest ranked match it was sent with
		resend: false,
		panels: {},
	};

	const Log = function(text) {
		$.Msg('[ShowMMR] ' + text);
	};

	/// cached lookup of the dashboard panels we care about
	const Find = function(id) {
		const cached = state.panels[id];
		if (cached && cached.IsValid())
			return cached;
		const dashboard = $('#Dashboard');
		const found = dashboard && dashboard.IsValid() ? dashboard.FindChildTraverse(id) : null;
		state.panels[id] = found;
		return found;
	};

	const FindStatsPage = function() {
		const pages = Find('DashboardPages');
		if (!pages)
			return null;
		for (let i = 0; i < pages.GetChildCount(); ++i) {
			const page = pages.GetChild(i);
			if (page && page.paneltype === 'DOTAProfileHeroStatsPage')
				return page;
		}
		return null;
	};

	const FindLastMatchBackground = function() {
		const manager = Find('DashboardBackgroundManager');
		if (!manager)
			return null;
		const queue = [manager];
		for (let depth = 0; depth < 3 && queue.length; ++depth) {
			for (let n = queue.length; n > 0; --n) {
				const panel = queue.shift();
				for (let i = 0; i < panel.GetChildCount(); ++i) {
					const child = panel.GetChild(i);
					if (child.paneltype === 'DOTADashboardBackgroundLastMatch')
						return child;
					queue.push(child);
				}
			}
		}
		return null;
	};

	/// --------------------------------------------------------------------------------------------
	/// history shared by the vscript side through CustomNetTables

	const LoadHistory = function() {
		const tables = CustomNetTables.GetAllTableValues('ShowMMR_history') || [];
		let count = 0;
		for (const entry of tables) {
			const kv = entry && entry.value;
			if (!kv)
				continue;
			for (const [k, v] of Object.entries(kv)) {
				state.history[parseInt(k)] = [v['1'], v['2']];
				++count;
			}
		}
		Log('history: ' + count + ' matches');
	};

	const HistoryUpdated = function(table, key, value) {
		if (value)
			state.history[parseInt(key)] = [value['1'], value['2']];
	};

	/// --------------------------------------------------------------------------------------------
	/// MMR value from the profile stats page

	const ReadMMR = function(page) {
		if (!page.BHasClass('MMRCalibrated'))
			return -1;
		return parseInt($.Localize('#ranked_mmr_value', page).replace(/\D+/g, '')) || -1;
	};

	/// time is the start of the latest ranked match on the stats page, 0 lets vscript ask the console instead
	const SendMMR = function(mmr, time) {
		if (mmr < 1)
			return;
		state.sent = mmr;
		state.sentTime = time;
		Log('mmr ' + mmr + ' after ranked match ' + time);
		try {
			GameEvents.SendCustomGameEventToServer('ShowMMR_Refresh', {mmr: mmr, time: time});
		} catch (e) {
			Log('send: ' + e);
		}
	};

	/// the vscript vm restarts after every game and can miss what was sent before it registered its listener,
	/// so send the last number again whenever it publishes its history
	const Resend = function() {
		if (state.sent < 1 || state.resend)
			return;
		state.resend = true;
		$.Schedule(1.0, function() {
			state.resend = false;
			SendMMR(state.sent, state.sentTime);
		});
	};

	/// The stats page has to be opened to get the number, and backend data only shows up there lazily,
	/// so open it hidden, poll the value, reload the page now and then, and go back where we were
	const Refresh = function(reason) {
		if (state.ui !== UI_STATE_DASHBOARD)
			return; /// never pull the dashboard over a running game, returning from it triggers a refresh anyway
		if (state.refreshing) {
			state.again = true;
			return;
		}
		state.refreshing = true;
		state.refreshed = true;
		Log('refresh: ' + reason);
		$.DispatchEvent('DOTAShowLocalProfileHeroStatsPage');

		let tries = 0, mmr = -1, time = 0;
		const poll = function() {
			const page = FindStatsPage();
			if (page) {
				page.style.visibility = 'collapse'; /// minimize flicker, the style is lost when the page reloads
				mmr = ReadMMR(page);
				time = LatestRankedTime(page);
			}
			++tries;
			/// the match list arrives from the backend separately from the number, give it a few more tries
			if ((mmr < 1 && tries < 40) || (mmr > 0 && time === 0 && tries < 12)) {
				if (page && tries % 8 === 0)
					$.DispatchEvent('DOTAReloadCurrentPage', page);
				$.Schedule(0.5, poll);
				return;
			}
			if (page) {
				page.style.visibility = 'visible';
				$.DispatchEvent('DOTANavigateBack', page);
			}
			if (mmr < 1)
				Log('refresh: no mmr after ' + tries + ' tries');
			SendMMR(mmr, time);
			state.refreshing = false;
			if (state.again) {
				state.again = false;
				ScheduleRefresh('queued', 5.0);
			}
		};
		$.Schedule(0.5, poll);
	};

	const ScheduleRefresh = function(reason, delay) {
		if (state.timer !== null)
			return;
		state.timer = $.Schedule(delay, function() {
			state.timer = null;
			Refresh(reason);
		});
	};

	/// --------------------------------------------------------------------------------------------
	/// match start time from a panel with {T:timestamp} and {T:d:duration} dialog variables

	/// The dialog variable value can't be read back, only localized, so derive candidate UTC times from the
	/// displayed date and the UTC time of day, then pick the one that localizes the same as the timestamp
	const MatchTime = function(panel, date) {
		const gmt = $.Localize('{T:d:timestamp}', panel), local = $.Localize('{T:timestamp}', panel);
		const hms = gmt.match(/\d+/g) || [], ymd = date.match(/\d+/g) || [];
		let hour = hms.length > 0 ? parseInt(hms[0]) : 0, minute = hms.length > 1 ? parseInt(hms[1]) : 0;
		let second = hms.length > 2 ? parseInt(hms[2]) : 0;
		if (hms.length < 3) { second = minute; minute = hour; hour = 0; }
		let year = ymd.length > 0 ? parseInt(ymd[0]) : 0, month = ymd.length > 1 ? parseInt(ymd[1]) : 0;
		let day = ymd.length > 2 ? parseInt(ymd[2]) : 0;
		if (year < 32) { const flip = day; day = year; year = flip; }
		if (year < 100) year += 2000;

		const utc = [];
		utc[0] = Date.UTC(year, month - 1, day, hour, minute, second) / 1000; /// day/month/year
		utc[1] = Date.UTC(year, day - 1, month, hour, minute, second) / 1000; /// month/day/year
		utc[2] = utc[0] - 86400; utc[3] = utc[0] + 86400; utc[4] = utc[1] - 86400; utc[5] = utc[1] + 86400;
		utc.forEach(function(t, i) { panel.SetDialogVariableTime('showmmr_utc' + i, t); });
		const candidates = $.Localize('{T:showmmr_utc0}|{T:showmmr_utc1}|{T:showmmr_utc2}|{T:showmmr_utc3}|{T:showmmr_utc4}|{T:showmmr_utc5}', panel).split('|');
		for (let i = 0; i < candidates.length; ++i) {
			if (candidates[i] === local)
				return utc[i];
		}
		return 0;
	};

	const Label = function(entry, abandoned) {
		if (!entry)
			return '';
		const [mmr, change] = entry;
		if (mmr === 0 && change === 0)
			return $.Localize('#dota_profile_recent_game_result_uncalibrated_ranked');
		const shift = (change > 0 ? ' (+' : ' (') + change + ')';
		if (abandoned)
			return $.Localize('#dota_profile_recent_game_result_abandon') + shift;
		return mmr + shift;
	};

	const Column = function(row, name) {
		const found = row.FindChildrenWithClassTraverse(name);
		return found && found.length ? found[0] : null;
	};

	/// ranked rows of the recent games table, newest first
	const RankedRows = function(page) {
		const table = page.FindChildTraverse('RecentGamesTable'), rows = [];
		if (!table)
			return rows;
		const ranked = $.Localize('#dota_lobby_type_competitive');
		for (let i = 0; i < table.GetChildCount(); ++i) {
			const row = table.GetChild(i);
			const type = row.BHasClass('RecentGame') ? Column(row, 'GameTypeColumn') : null;
			if (type && type.text === ranked)
				rows.push(row);
		}
		return rows;
	};

	/// match start time of a row, cached by the displayed date, time and duration
	const RowTime = function(row) {
		const date = Column(row, 'TimestampDate'), time = Column(row, 'TimestampTime'), duration = Column(row, 'DurationColumn');
		if (!date)
			return 0;
		const stamp = 'E' + (date.text + (time ? time.text : '') + (duration ? duration.text : '')).replace(/\D/g, '');
		if (state.rows[stamp] === undefined) {
			state.rows[stamp] = MatchTime(row, date.text);
			Log('row ' + date.text + ' ' + (time ? time.text : '') + ' -> ' + state.rows[stamp]);
		}
		return state.rows[stamp];
	};

	const LatestRankedTime = function(page) {
		const rows = RankedRows(page);
		return rows.length ? RowTime(rows[0]) : 0;
	};

	/// --------------------------------------------------------------------------------------------
	/// stats page: replace the Win / Loss text of ranked matches with "12345 (+45)"

	const PatchStatsPage = function(page) {
		for (const row of RankedRows(page)) {
			const result = Column(row, 'ResultColumn');
			if (!result)
				continue;
			const text = Label(state.history[RowTime(row)], row.BHasClass('Abandoned'));
			if (text !== '' && result.text !== text)
				result.text = text;
		}
	};

	/// last match background: show the change instead of Win / Loss
	const PatchLastMatch = function(background) {
		const date = $.Localize('{T:s:timestamp}', background);
		const stamp = 'E' + (date + $.Localize('{T:t:timestamp}{T:d:duration}', background)).replace(/\D/g, '');
		if (state.rows[stamp] === undefined)
			state.rows[stamp] = MatchTime(background, date);
		const entry = state.history[state.rows[stamp]];
		if (!entry || entry[0] === 0)
			return;
		const text = (entry[1] > 0 ? '+' : '') + entry[1];
		for (const id of ['Win', 'Loss']) {
			const label = background.FindChildTraverse(id);
			if (label && label.text !== text)
				label.text = text;
		}
	};

	const Tick = function() {
		$.Schedule(1.0, Tick);
		try {
			const page = FindStatsPage();
			if (page) {
				PatchStatsPage(page);
				/// opening the stats page by hand also records the number
				if (!state.refreshing) {
					const mmr = ReadMMR(page), time = LatestRankedTime(page);
					if (mmr > 0 && time > 0 && (mmr !== state.sent || time !== state.sentTime))
						SendMMR(mmr, time);
				}
			}
			const background = FindLastMatchBackground();
			if (background)
				PatchLastMatch(background);
		} catch (e) {
			Log('tick: ' + e);
		}
	};

	/// --------------------------------------------------------------------------------------------

	$.RegisterForUnhandledEvent('DOTAGameUIStateChanged', function(oldState, newState) {
		state.ui = newState;
		if (oldState === UI_STATE_LOADING_SCREEN && newState === UI_STATE_DASHBOARD)
			ScheduleRefresh('back at dashboard', 3.0);
	});
	$.RegisterForUnhandledEvent('DOTARankUpdated', function() {
		ScheduleRefresh('rank updated', 2.0);
	});
	CustomNetTables.SubscribeNetTableListener('ShowMMR_history', function() { LoadHistory(); Resend(); });
	CustomNetTables.SubscribeNetTableListener('ShowMMR_update', HistoryUpdated);

	LoadHistory();
	$.Schedule(1.0, Tick);
	/// get a baseline number once per session, unless an event already did
	$.Schedule(20.0, function() {
		if (!state.refreshed)
			ScheduleRefresh('startup', 0.1);
	});
	Log('loaded');
})();
