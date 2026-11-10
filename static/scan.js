// 扫描式接入（?scan=2 灰度开关）：进入「本机 Agent」自动扫描，按「能不能用」分组，每行只有一个主动作。
// 数据来自 /api/agents/scan 与 /api/agents/connect（bridge/scan.py）。
// 旧的「选择程序」下拉框流程保留为回退：地址带 scan=1、或点「手动指定程序」回到旧界面。
'use strict';

function scanV2Enabled() {
  try {
    const flag = new URLSearchParams(location.search).get('scan');
    if (flag === '2' || flag === '1') sessionStorage.setItem('cojam_scan2', flag === '2' ? '1' : '0');
  } catch (e) {}
  try { return sessionStorage.getItem('cojam_scan2') === '1'; } catch (e) { return false; }
}

function scanEnable() {
  /* 入口扁平化和首次使用引导点进来时显式选择扫描流程：打开开关。 */
  try { sessionStorage.setItem('cojam_scan2', '1'); } catch (e) {}
  return true;
}

function scanCandidate(w, id) {
  return ((w.scan && w.scan.candidates) || []).find(c => c.id === id) || null;
}

// ---------- 入口：接管「本机 Agent」第 2 步 ----------
function renderScanSetup(w) {
  w.scan = w.scan || null;
  w.scanDrawn = '';
  w.scanStarted = w.scanStarted || false;
  wizardFrame(w, '<div id="scanPage"><h3>连接本机 AI</h3><p class="hint">在这台电脑上查找可以协作的 AI，选一个直接连接。</p><div id="scanBody"><p class="hint" role="status">正在准备扫描…</p></div></div>'
    + '<div class="btns connection-actions"><button id="connBack">更换接入方式</button></div>');
  document.getElementById('connBack').onclick = () => startConnectionWizard();
  if (!w.scanStarted) {
    w.scanStarted = true;
    scanStart(w, false);
  } else scanPoll(w);
  drawScan(w);
}

async function scanStart(w, force) {
  wizardError('');
  const [ok, data] = await post('/api/agents/scan', {force: !!force});
  if (!wizardCurrent(w)) return;
  if (!ok) {
    w.scan = null; w.scanError = data.error || '无法开始扫描';
    drawScan(w);
    return;
  }
  w.scan = data; w.scanError = '';
  drawScan(w);
  scanPoll(w);
}

function scanPoll(w) {
  clearTimeout(w.timer);
  const tick = async () => {
    if (!wizardCurrent(w) || w.step !== 2 || !document.getElementById('scanPage')) return;
    let ok = true, data = null, error = '';
    try {
      const [got, body] = await connectionGet('/api/agents/scan');
      ok = got; data = body;
      if (!ok) error = body.error || '读取扫描结果失败';
    } catch (e) { ok = false; error = e.message || '读取扫描结果失败'; }
    if (!wizardCurrent(w) || w.step !== 2 || !document.getElementById('scanPage')) return;
    if (ok) { w.scan = data; w.scanError = ''; } else w.scanError = error;
    if (!w.connecting && !w.sub) drawScan(w);   // 确认层开着时只更新数据，不重绘输入框
    if (!ok || ['idle', 'scanning'].includes((w.scan || {}).state)) w.timer = setTimeout(tick, ok ? 600 : 2000);
  };
  w.timer = setTimeout(tick, 400);
}

function scanLegacy(w) {
  clearTimeout(w.timer);
  clearInterval(w.extTimer);
  w.legacy = true; w.sub = null; w.ext = null; w.connecting = null; w.scanStarted = false;
  renderConnectionWizard();
}

// ---------- 主列表 ----------
function scanRowGroups(rows) {
  return {
    ready: rows.filter(r => r.status === 'ready'),
    attention: rows.filter(r => r.status === 'attention'),
    connected: rows.filter(r => r.status === 'connected'),
    desktop: rows.filter(r => r.kind === 'desktop'),
    missing: rows.filter(r => r.status === 'missing')
  };
}

function scanInstallText(row) {
  const installs = row.installations || [];
  if (!installs.length) return '';
  const chosen = installs.find(i => i.selected) || installs[0];
  const more = installs.length > 1 ? ' · ' + installs.length + ' 个安装' : '';
  return chosen.path + (row.version ? ' · ' + row.version : '') + more;
}

function scanRowHtml(row) {
  const info = [];
  let title = '<b>' + esc(row.label) + '</b>';
  if (row.version) title += ' <small>' + esc(row.version) + '</small>';
  if (row.kind === 'desktop') {
    const words = {desktop_cli: '已安装 · 自带 CLI 已找到', desktop_external: '已安装 · 可作为外部成员接入',
      desktop_only: '已安装 · 会议室不能直接调用'};
    info.push('<small class="scan-badge">' + esc(words[row.status] || row.status) + '</small>');
  }
  const path = scanInstallText(row);
  if (path) info.push('<small class="scan-path">' + esc(path) + '</small>');
  if (row.note) info.push('<small class="scan-note">' + esc(row.note) + '</small>');
  const rooms = row.rooms || [];
  if (rooms.length) info.push('<small>已加入：' + esc(rooms.map(r => r.title || r.id).join('、')) + '</small>');
  if (row.kind === 'desktop' && row.status === 'desktop_cli' && row.cli) {
    const cliRooms = row.cli.rooms || [];
    if (cliRooms.length) info.push('<small>CLI 已加入：' + esc(cliRooms.map(r => r.title || r.id).join('、')) + '</small>');
  }
  let action = '';
  if (row.status === 'ready' || row.status === 'attention')
    action = '<button class="pri" data-scan-connect="' + esc(row.id) + '">' + (row.status === 'attention' ? '仍然连接' : '连接') + '</button>';
  else if (row.status === 'connected')
    action = '<button data-scan-manage="' + esc(row.id) + '">管理</button>';
  else if (row.status === 'missing')
    action = '<button data-scan-manual="' + esc(row.id) + '">手动指定程序</button>';
  else if (row.status === 'desktop_cli')
    action = '<button class="pri" data-scan-connect="' + esc(row.id) + '">连接 CLI</button>';
  else if (row.status === 'desktop_external')
    action = '<button class="pri" data-scan-external="' + esc(row.id) + '">作为外部成员接入</button>';
  else if (row.status === 'desktop_only')
    action = '<button data-scan-help="' + esc(row.id) + '">查看接入方式</button>';
  return '<div class="scan-row"><span class="scan-info">' + title + info.join('') + '</span><span class="scan-acts">' + action + '</span></div>';
}

function scanGroupHtml(title, rows) {
  if (!rows || !rows.length) return '';
  return '<h4 class="scan-group-title">' + esc(title) + '</h4><div class="scan-group">' + rows.map(scanRowHtml).join('') + '</div>';
}

function drawScan(w) {
  const body = document.getElementById('scanBody');
  if (!body || !wizardCurrent(w)) return;
  if (w.connecting) return drawScanConnect(w);
  if (w.sub === 'external') return drawScanExternal(w);
  if (w.sub === 'help') return drawScanHelp(w);
  const scan = w.scan;
  const state = scan ? scan.state : (w.scanError ? 'error' : 'idle');
  const signature = JSON.stringify([state, scan && scan.progress, scan && scan.counts, scan && scan.candidates,
    scan && scan.log && scan.log.length, scan && scan.errors, w.scanError]);
  if (signature === w.scanDrawn) return;
  w.scanDrawn = signature;
  const top = mbox.scrollTop;
  const counts = (scan && scan.counts) || {};
  let html = '';
  if (state === 'error') {
    html += '<div class="connection-evidence failure"><b>扫描没跑成</b><p>' + esc((scan && scan.errors || []).join('；') || w.scanError || '未知错误') + '。可以点下面的「重新扫描」再试一次。</p></div>';
  } else if (state === 'idle' || state === 'scanning') {
    const progress = Math.round(((scan && scan.progress) || 0) * 100);
    const found = (counts.ready || 0) + (counts.attention || 0) + (counts.connected || 0);
    html += '<div class="scan-progress" role="status"><div class="scan-bar"><i style="width:' + progress + '%"></i></div>'
      + '<small>正在扫描 · 已找到 ' + found + ' 个程序、' + (counts.desktop || 0) + ' 个桌面应用</small></div>';
  } else {
    const bits = [];
    if (counts.ready) bits.push(counts.ready + ' 个可直接连接');
    if (counts.attention) bits.push(counts.attention + ' 个需要确认');
    if (counts.desktop) bits.push(counts.desktop + ' 个桌面应用');
    if (counts.connected) bits.push(counts.connected + ' 个已在会话');
    html += '<p class="hint" role="status">扫描完成' + (bits.length ? '：' + bits.join('、') : '') + (state === 'partial' ? ' · 有些程序没检测成功' : '') + '。</p>';
    if (state === 'partial' && (scan.errors || []).length)
      html += '<div class="connection-evidence failure"><b>部分程序检测失败</b><p>' + esc(scan.errors.join('；')) + '</p></div>';
  }
  const rows = (scan && scan.candidates) || [];
  const groups = scanRowGroups(rows);
  html += scanGroupHtml('可以直接连接', groups.ready);
  html += scanGroupHtml('需要确认', groups.attention);
  html += scanGroupHtml('已加入会话', groups.connected);
  html += scanGroupHtml('桌面应用（不能直接接收任务）', groups.desktop);
  html += scanGroupHtml('之前接入过 · 程序没找到', groups.missing);
  if ((state === 'done' || state === 'partial') && !rows.length)
    html += '<div class="connection-evidence pending"><b>没有检测到可调用的程序</b><p>可以先手动指定一个程序，或装好之后点「重新扫描」。</p></div>';
  const log = (scan && scan.log) || [];
  html += '<div class="scan-log"><small>扫描日志</small><ul>' + log.map(l => '<li>' + esc((l.time || '').slice(11)) + ' ' + esc(l.text) + '</li>').join('') + '</ul></div>';
  html += '<div class="btns scan-footer">';
  if ((state === 'done' || state === 'partial') && groups.ready.length)
    html += '<button id="scanAll" class="pri">全部接入（' + groups.ready.length + '）</button>';
  html += '<button id="scanManual">手动指定程序</button><button id="scanRescan"' + (state === 'scanning' ? ' disabled' : '') + '>' + (state === 'scanning' ? '扫描中…' : '重新扫描') + '</button></div>';
  body.innerHTML = html;
  mbox.scrollTop = top;
  const all = document.getElementById('scanAll');
  if (all) all.onclick = () => scanConnectAll(w);
  document.getElementById('scanManual').onclick = () => scanLegacy(w);
  document.getElementById('scanRescan').onclick = () => { if ((w.scan || {}).state !== 'scanning') scanStart(w, true); };
  mbox.querySelectorAll('[data-scan-connect]').forEach(b => b.onclick = () => { w.connecting = b.dataset.scanConnect; w.scanDrawn = ''; drawScan(w); });
  mbox.querySelectorAll('[data-scan-manage]').forEach(b => b.onclick = () => openConnection(b.dataset.scanManage));
  mbox.querySelectorAll('[data-scan-manual]').forEach(b => b.onclick = () => scanLegacy(w));
  mbox.querySelectorAll('[data-scan-external]').forEach(b => b.onclick = () => { w.ext = scanCandidate(w, b.dataset.scanExternal); w.sub = 'external'; w.scanDrawn = ''; drawScan(w); });
  mbox.querySelectorAll('[data-scan-help]').forEach(b => b.onclick = () => { w.help = b.dataset.scanHelp; w.sub = 'help'; w.scanDrawn = ''; drawScan(w); });
}

// ---------- 连接确认层 ----------
function drawScanConnect(w) {
  const body = document.getElementById('scanBody');
  if (!body) return;
  const row = scanCandidate(w, w.connecting);
  if (!row) { w.connecting = null; w.scanDrawn = ''; return drawScan(w); }
  const target = row.kind === 'desktop' && row.cli ? row.cli : row;
  const installs = target.installations || [];
  const rooms = (typeof ROOMS !== 'undefined' ? ROOMS : []).filter(r => !r.archived);
  const existing = (typeof CONTACTS !== 'undefined' ? CONTACTS.find(c => c.id === target.id) : null) || {};
  const top = mbox.scrollTop;
  body.innerHTML = '<div class="scan-connect"><h4>连接 ' + esc(target.label) + '</h4>'
    + (row.kind === 'desktop' ? '<p class="hint">桌面应用本身不能被会议室调用；这里连接的是它自带的命令行程序。</p>' : '')
    + (row.status === 'attention' && row.note ? '<p class="hint">' + esc(row.note) + '。可以先连接，稍后在会话里确认它能正常回复。</p>' : '')
    + (installs.length > 1 ? '<label>调用的安装<select id="scanInstall">' + installs.map(i => '<option value="' + esc(i.id) + '"' + (i.selected ? ' selected' : '') + '>' + esc(i.path) + (i.version ? ' · ' + esc(i.version) : '') + '</option>').join('') + '</select></label>' : '')
    + '<label>联系人名称<input id="scanName" maxlength="40" value="' + esc(existing.label || target.label) + '"></label>'
    + '<label>加入会话<select id="scanRoom"><option value="">暂不加入，稍后从会话里添加</option>'
    + rooms.map(r => '<option value="' + esc(r.id) + '">' + esc(r.title) + '</option>').join('') + '</select></label>'
    + (rooms.length ? '' : '<p class="hint">现在还没有会话；连接后会保留在联系人列表里，建会话时再添加。</p>')
    + '<p id="scanConnectError" role="alert"></p>'
    + '<div class="btns"><button id="scanConnectCancel">取消</button><button id="scanConnectGo" class="pri">连接</button></div></div>';
  mbox.scrollTop = top;
  document.getElementById('scanConnectCancel').onclick = () => { w.connecting = null; w.scanDrawn = ''; drawScan(w); };
  document.getElementById('scanConnectGo').onclick = () => submitScanConnect(w);
}

async function scanConnectAll(w) {
  /* 首次使用引导的「全部接入」：把扫描到的可直接连接的程序全接上（默认名称、暂不入会话）。 */
  const rows = scanRowGroups((w.scan && w.scan.candidates) || []).ready;
  const all = document.getElementById('scanAll');
  if (!rows.length || !all || all.disabled) return;
  all.disabled = true; all.textContent = '接入中…';
  let ok = 0;
  const failed = [];
  for (const row of rows) {
    const [done, data] = await post('/api/agents/connect', {candidate: row.id, label: row.label, room: ''});
    if (!wizardCurrent(w)) return;
    if (done) { ok++; row.note = row.note || '已接入，可在会话里添加'; }
    else failed.push(row.label + '：' + ((data || {}).error || '失败'));
  }
  w.scanDrawn = '';
  drawScan(w);
  refreshContacts();
  if (typeof pollRooms === 'function') pollRooms();
  wizardError(failed.length ? '已接入 ' + ok + ' 个；失败的没动：' + failed.join('；') : '已接入 ' + ok + ' 个程序。建会话时勾上它们就能开工。');
}

async function submitScanConnect(w) {  const row = scanCandidate(w, w.connecting);
  if (!row) { w.connecting = null; w.scanDrawn = ''; return drawScan(w); }
  const select = document.getElementById('scanInstall');
  const name = document.getElementById('scanName');
  const room = document.getElementById('scanRoom');
  const error = document.getElementById('scanConnectError');
  const go = document.getElementById('scanConnectGo');
  const body = {candidate: row.id,
    installation: select ? select.value : '',
    label: name ? name.value.trim() : '',
    room: room && room.value ? room.value : ''};
  wizardError('');
  go.disabled = true; go.textContent = '连接中…';
  const [ok, data] = await post('/api/agents/connect', body);
  if (!wizardCurrent(w)) return;
  if (!ok) {
    go.disabled = false; go.textContent = '连接';   // 名称和选择都留在输入框里，只改错的地方
    error.textContent = data.error || '连接失败，请重试';
    return;
  }
  const entry = body.room ? {id: body.room, title: ((typeof ROOMS !== 'undefined' ? ROOMS : []).find(r => r.id === body.room) || {}).title || '会话'} : null;
  if (row.kind === 'desktop' && row.cli) {
    if (entry) { row.cli.status = 'connected'; row.cli.rooms = [entry].concat(row.cli.rooms || []); }
    else row.note = row.note || 'CLI 已接入，可在会话里添加';
  } else if (entry) {
    row.status = 'connected'; row.rooms = [entry].concat(row.rooms || []);
  } else row.note = row.note || '已接入，可在会话里添加';
  w.connecting = null;
  w.scanDrawn = '';
  drawScan(w);
  refreshContacts();
  if (typeof pollRooms === 'function') pollRooms();
}

// ---------- 桌面应用：外部成员简化流程 ----------
function drawScanExternal(w) {
  const body = document.getElementById('scanBody');
  if (!body) return;
  const row = w.ext;
  if (!row) { w.sub = null; w.scanDrawn = ''; return drawScan(w); }
  w.base = connectionDefaultBase('external', w.info, location.origin);
  const hosts = ((typeof agCat !== 'undefined' && agCat.hosts) || []).filter(h => h.present);
  const top = mbox.scrollTop;
  body.innerHTML = '<div class="scan-connect"><h4>把 ' + esc(row.label) + ' 接成外部成员</h4>'
    + '<p class="hint">桌面应用不能被会议室直接调用。接入后它作为会外成员参与：在它那边的软件里主动发言、接收 @，会议室不会直接叫它。</p>'
    + '<div class="connection-identity"><b>' + esc(row.label) + '</b><small>@' + esc(row.id) + '</small></div>'
    + '<label>参与方式<select id="scanExtMode"><option value="auto"' + (w.memberMode !== 'manual' ? ' selected' : '') + '>自动接收任务并回复（推荐）</option><option value="manual"' + (w.memberMode === 'manual' ? ' selected' : '') + '>通过 MCP 手动参与</option></select></label>'
    + '<div id="scanExtHostWrap"' + (w.memberMode === 'manual' ? '' : ' hidden') + '><label>对方使用的软件<select id="connHost"><option value="custom">自定义 / 其他 MCP 客户端</option>'
    + hosts.map(h => '<option value="' + esc(h.id) + '"' + (w.host === h.id ? ' selected' : '') + '>' + esc(h.label) + '</option>').join('') + '</select></label></div>'
    + '<label>这个应用要访问的地址<input id="scanExtBase" value="' + esc(w.base) + '"></label>'
    + '<p class="hint" id="scanExtBaseHint"></p>'
    + '<div id="scanExtStatus" class="connection-evidence pending"></div>'
    + '<div id="connCredentials"></div>'
    + '<p id="scanConnectError" role="alert"></p>'
    + '<div class="btns"><button id="scanExtCancel">返回</button><button id="scanExtGo" class="pri">' + (w.token ? '重新生成接入配置' : '生成接入配置') + '</button></div></div>';
  mbox.scrollTop = top;
  const baseHint = document.getElementById('scanExtBaseHint');
  const showBaseHint = () => {
    const issue = connectionBaseWarning(w.base);
    baseHint.textContent = issue || '只发给这台应用的使用者；跨网络需要对方能打开这个地址。';
    baseHint.classList.toggle('warn', !!issue);
  };
  showBaseHint();
  document.getElementById('scanExtBase').oninput = e => {
    w.base = e.target.value.trim().replace(/\/+$/, '');
    showBaseHint();
    if (w.token) renderConnectionCredentials(w);
  };
  document.getElementById('scanExtMode').onchange = e => {
    w.memberMode = e.target.value;
    document.getElementById('scanExtHostWrap').hidden = w.memberMode !== 'manual';
    if (w.token) renderConnectionCredentials(w);
  };
  document.getElementById('connHost').onchange = e => { w.host = e.target.value; if (w.token) renderConnectionCredentials(w); };
  document.getElementById('scanExtCancel').onclick = () => { clearInterval(w.extTimer); w.sub = null; w.ext = null; w.scanDrawn = ''; drawScan(w); };
  document.getElementById('scanExtGo').onclick = () => submitScanExternal(w);
  if (w.token) renderConnectionCredentials(w);   // 复用旧界面的成员连接端 / MCP 配置展示
  scanExtStatus(w);
  clearInterval(w.extTimer);
  w.extTimer = setInterval(async () => {
    if (!wizardCurrent(w) || w.sub !== 'external') { clearInterval(w.extTimer); return; }
    const [ok, j] = await post('/api/contacts/status', {id: w.ext.id});
    if (ok && wizardCurrent(w) && w.sub === 'external') { w.status = j.connection; w.member = j.member; scanExtStatus(w); }
  }, 2500);
}

function scanExtStatus(w) {
  const box = document.getElementById('scanExtStatus');
  if (!box) return;
  const member = w.member || ((typeof agCat !== 'undefined' && agCat.members) || {})[w.ext.id] || {};
  const conn = w.status || ((typeof agCat !== 'undefined' && agCat.connections) || {})[w.ext.id] || {};
  let label, good;
  if (member.automatic) { label = '✓ 自动响应已连接：被 @ 后会自动处理并回复'; good = true; }
  else if (conn.state === 'connected') { label = '✓ 消息通道已连接；开启成员连接端后才能自动响应'; good = true; }
  else if (w.token) { label = '等待对方导入配置并连接…'; good = false; }
  else { label = '生成配置后，把它导入这个应用里使用的 Harness。'; good = false; }
  box.className = 'connection-evidence ' + (good ? 'success' : 'pending');
  box.innerHTML = '<b>' + esc(label) + '</b>';
}

async function submitScanExternal(w) {
  const go = document.getElementById('scanExtGo');
  const error = document.getElementById('scanConnectError');
  wizardError('');
  go.disabled = true; go.textContent = '正在生成…';
  try {
    if (w.ext.preset && !(typeof CONTACTS !== 'undefined' && CONTACTS.some(c => c.id === w.ext.id))) {
      const [restored, r1] = await post('/api/agents/restore', {id: w.ext.id});
      if (!restored) throw Error(r1.error || '恢复联系人失败');
      await refreshContacts();
    }
    const [ok, r2] = await post('/api/contacts/key', {id: w.ext.id});
    if (!ok) throw Error(r2.error || '生成失败');
    w.token = r2.token;
    w.member = {automatic: false};
    w.status = {state: 'waiting', label: '等待对方连接', has_key: true};
    w.scanDrawn = '';
    drawScan(w);   // 重新渲染，凭据区出现（复用旧界面的成员连接端 / MCP 配置展示）
    await refreshContacts();
  } catch (e) {
    if (!wizardCurrent(w)) return;
    go.disabled = false; go.textContent = '生成接入配置';
    error.textContent = e.message || '生成失败';
  }
}

// ---------- 桌面应用：查看接入方式 ----------
function drawScanHelp(w) {
  const body = document.getElementById('scanBody');
  if (!body) return;
  const row = scanCandidate(w, w.help);
  if (!row) { w.sub = null; w.scanDrawn = ''; return drawScan(w); }
  const top = mbox.scrollTop;
  body.innerHTML = '<div class="scan-connect"><h4>' + esc(row.label) + ' 怎么接入？</h4>'
    + '<p class="hint">会议室不能直接调用桌面应用；能接的是下面两种方式之一：</p>'
    + '<ol class="connection-instructions"><li><b>它自带的命令行程序</b> — 装好后点「重新扫描」，这一行会变成「连接 CLI」。</li>'
    + '<li><b>作为外部成员</b> — 应用里的 agent 通过成员连接端 / MCP 参与会话：它主动发言、接收 @，会议室不直接叫它。</li></ol>'
    + (row.linked_cli ? '<p class="hint">自带的 CLI（' + esc(row.linked_cli) + '）这台电脑上还没找到。</p>' : '')
    + '<p id="scanConnectError" role="alert"></p>'
    + '<div class="btns"><button id="scanHelpBack">返回</button><button id="scanHelpExternal" class="pri">作为外部成员接入</button><button id="scanHelpRescan">重新扫描</button></div></div>';
  mbox.scrollTop = top;
  document.getElementById('scanHelpBack').onclick = () => { w.sub = null; w.scanDrawn = ''; drawScan(w); };
  document.getElementById('scanHelpExternal').onclick = () => { w.ext = row; w.sub = 'external'; w.scanDrawn = ''; drawScan(w); };
  document.getElementById('scanHelpRescan').onclick = () => { w.sub = null; w.scanDrawn = ''; scanStart(w, true); };
}
