// Guided onboarding. Credentials stay in this modal's memory and never in localStorage.
let connectionWizard = null, connectionSequence = 0;
async function checkConnectionBackend() {
  let info;
  try {
    const response = await fetch('/api/server/info');
    if (!response.ok) throw Error();
    info = await response.json();
  } catch (e) { throw Error('当前仍在运行旧版后台，无法配置接入。请退出旧后台并重新打开合拍 Cojam；只刷新网页不会更新后台。'); }
  if (info.app !== 'AI Bridge' || info.version !== '2.6.1' || info.api_revision !== 2)
    throw Error('界面与后台版本不一致。请重新打开最新的合拍 Cojam，再刷新此页面。');
  return info;
}
function connectionDefaultBase(type, info, origin) {
  if (!['user', 'external', 'mydevice'].includes(type)) return origin;
  try {
    const host = new URL(origin).hostname.replace(/^\[|\]$/g, '').toLowerCase();
    // When already using a remote address, it is the only address known to
    // work from this device; replacing it with the server's LAN breaks tunnels.
    if (!['127.0.0.1', 'localhost', '::1'].includes(host)) return origin;
  } catch (e) { return origin; }
  if (type === 'mydevice') return (info && info.tailscale_url) || (info && info.lan_url) || origin;
  return (info && info.lan_url) || origin;
}
function connectionBaseWarning(base) {
  try {
    const host = new URL(base).hostname.replace(/^\[|\]$/g, '');
    if (['127.0.0.1', 'localhost', '::1'].includes(host)) return '这是本机地址，只有这台电脑能打开；手机和别的设备连不上。';
  } catch (e) { return '地址格式不对，请填写完整地址，例如 http://192.168.1.5:8766。'; }
  return '';
}
const CONNECTION_TYPES = {
  mydevice: ['我的设备', '手机、平板或另一台电脑获得完整界面，并以「我」的身份参与。', uiIcon('device')],
  local: ['本机 Agent', '检测这台电脑上的 Claude、Codex 或自定义 CLI，直接调用，不需要接入密钥。', uiIcon('terminal')],
  external: ['其他设备上的 Harness', '作为同一个会议室的成员，自动接收任务、回复，并互相 @。', uiIcon('device')],
  http: ['HTTP 服务', '填写你自己的 Harness 服务地址，由本应用主动调用并测试回复。', uiIcon('link')],
  user: ['其他用户', '生成邀请票；对方打开链接领取身份，只进指定会话。同一设备之后仍是同一个人。', uiIcon('user')]
};
function stopConnectionWizard() {
  if (connectionWizard) { clearInterval(connectionWizard.timer); connectionWizard.token = ''; }
  connectionWizard = null;
}
function connectionModalChanged() {
  const box = document.getElementById('connectionWizard');
  if (connectionWizard && (!box || box.dataset.flow !== String(connectionWizard.seq))) stopConnectionWizard();
}
async function connectionGet(url) {
  const response = await fetch(url);
  let data = {};
  try { data = await response.json(); } catch (e) {}
  return [response.ok, data];
}
async function renderDeviceSetup(w) {
  const select = document.getElementById('connDeviceBase');
  const custom = document.getElementById('connDeviceCustomBase');
  const hint = document.getElementById('connDeviceBaseHint');
  const options = [];
  if (w.info && w.info.tailscale_url) options.push([w.info.tailscale_url, 'Tailscale（完整界面）']);
  if (w.info && w.info.lan_url) options.push([w.info.lan_url, '局域网']);
  options.push([location.origin, '当前地址']);
  select.innerHTML = options.map(([value, label]) => '<option value="' + esc(value) + '">' + esc(label) + '</option>').join('')
    + '<option value="__custom__">自定义 HTTPS 地址</option>';
  const known = options.some(([value]) => value === w.base);
  select.value = known ? w.base : '__custom__';
  custom.value = known ? '' : w.base;
  custom.hidden = known;
  const showHint = () => {
    const issue = connectionBaseWarning(w.base);
    hint.textContent = issue || '用要接入的设备扫描二维码或打开链接。只发给可信设备。';
    hint.classList.toggle('warn', !!issue);
  };
  select.onchange = () => {
    custom.hidden = select.value !== '__custom__';
    w.base = select.value === '__custom__' ? custom.value.trim().replace(/\/+$/, '') : select.value;
    showHint();
  };
  custom.oninput = () => { w.base = custom.value.trim().replace(/\/+$/, ''); showHint(); };
  showHint();
  document.getElementById('connDeviceCreate').onclick = () => generateDeviceCode(w);
  try {
    const [ok, data] = await connectionGet('/api/devices');
    if (!ok) throw Error(data.error || '读取设备失败');
    w.account = data.account;
    w.devices = data.devices || [];
    renderDeviceList(w);
  } catch (error) {
    document.getElementById('connDeviceList').textContent = error.message;
  }
}
function renderDeviceList(w) {
  const box = document.getElementById('connDeviceList');
  if (!box) return;
  box.innerHTML = (w.devices || []).map(device =>
    '<div class="connection-device"><span><b>' + esc(device.label) + '</b><small>'
    + (device.online ? '在线' : '最近在线 ' + new Date((device.last_seen || device.created) * 1000).toLocaleString())
    + ' · @' + esc(device.id) + '</small></span>'
    + (device.current ? '<small>当前设备</small>' : '<button data-device-revoke="' + esc(device.id) + '">撤销</button>')
    + '</div>').join('') || '<p class="hint">还没有接入其他设备。</p>';
  box.querySelectorAll('[data-device-revoke]').forEach(button => {
    button.onclick = () => revokeDevice(w, button.dataset.deviceRevoke);
  });
}
async function revokeDevice(w, id) {
  if (!confirm('撤销后，这台设备下次请求就会失去完整界面。继续？')) return;
  const [ok, data] = await post('/api/devices/revoke', {id});
  if (!ok) return wizardError(data.error || '撤销失败');
  const [listOk, listing] = await connectionGet('/api/devices');
  if (listOk) { w.devices = listing.devices || []; renderDeviceList(w); }
}
async function generateDeviceCode(w) {
  if (w.busy) return;
  wizardError('');
  const nameInput = document.getElementById('connDeviceName');
  const baseSelect = document.getElementById('connDeviceBase');
  if (nameInput) w.form.deviceName = nameInput.value;
  if (baseSelect) w.base = baseSelect.value === '__custom__'
    ? document.getElementById('connDeviceCustomBase').value.trim().replace(/\/+$/, '') : baseSelect.value;
  try {
    const url = new URL(w.base);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw 0;
  } catch (e) { wizardError('请填写完整的服务根地址，例如 https://example.ts.net'); return; }
  setConnectionBusy(w, true);
  try {
    const [ok, data] = await post('/api/devices/code', {
      label: connectionDeviceName(w.form.deviceName), base: w.base
    });
    if (!ok) throw Error(data.error || '生成失败');
    const issue = connectionBaseWarning(w.base);
    const box = document.getElementById('connDeviceResult');
    box.innerHTML = '<div class="connection-evidence success"><b>✓ 接入码已生成</b>'
      + '<p>10 分钟内有效，只能使用一次。用要接入的设备扫描二维码或打开链接。</p></div>'
      + (issue ? '<p class="hint connection-base-warning">' + esc(issue) + ' 先换一个设备能打开的地址。</p>'
               : '<div id="connQr" class="connection-qr" aria-label="设备接入二维码"></div>')
      + '<input id="connDeviceUrl" readonly aria-label="设备接入链接"><button id="connDeviceCopy">复制接入链接</button>';
    document.getElementById('connDeviceUrl').value = data.url || '';
    document.getElementById('connDeviceCopy').onclick = async event => {
      await copyText(data.url || ''); event.target.textContent = '已复制';
    };
    if (!issue) renderConnectionQrUrl('connQr', data.url || connectionDeviceUrl(w.base, data.code));
  } catch (error) {
    wizardError(error.message);
  } finally {
    if (wizardCurrent(w)) setConnectionBusy(w, false);
  }
}
function renderConnectionQrUrl(id, text) {
  const box = document.getElementById(id);
  if (!box || typeof QRCode !== 'function') return;
  try {
    new QRCode(box, {text, width: 168, height: 168, correctLevel: QRCode.CorrectLevel.M});
  } catch (error) { box.textContent = '二维码没生成出来，请直接用链接。'; }
}
async function startConnectionWizard(id, type) {
  stopConnectionWizard();
  document.body.classList.remove('nav-open');
  const w = {seq: ++connectionSequence, step: 1, type: type || '', id: id || '', token: '', base: location.origin,
    form: {}, status: null, busy: false, permissions: {read: true, post: true, trigger: true, board: false, files: false, work: false}};
  connectionWizard = w;
  try {
    const info = await checkConnectionBackend();
    await refreshContacts();
    if (connectionWizard !== w) return;
    const contact = CONTACTS.find(a => a.id === id);
    if (contact) {
      w.type = contact.kind === 'user' ? 'user' : contact.driver === 'external' ? 'external' : contact.driver === 'http' ? 'http' : 'local';
      w.status = (agCat.connections || {})[id] || null;
      w.localChoice = id; w.saved = true; w.step = 2;
    } else if (type) w.step = 2;
    w.info = info;
    w.base = connectionDefaultBase(w.type, info, location.origin);
    renderConnectionWizard();
  } catch (e) { stopConnectionWizard(); showModal('<h3>接入暂不可用</h3><p role="alert">' + esc(e.message || '无法读取联系人') + '</p><div class="btns"><button onclick="location.reload()">刷新页面</button><button onclick="hideModal()">关闭</button></div>'); }
}
function addContact() { return startConnectionWizard(); }
function openConnection(id) { return startConnectionWizard(id); }
function connectionContact(w) { return CONTACTS.find(a => a.id === w.id); }
function wizardCurrent(w) { return connectionWizard === w && !!document.getElementById('connectionWizard'); }
function wizardError(message) { const e = document.getElementById('connError'); if (e) e.textContent = message || ''; }
function wizardFrame(w, content) {
  const steps = ['选择方式', '配置与验证', '会话与权限', '完成'];
  showModal('<div id="connectionWizard" data-flow="' + w.seq + '"><div class="connection-heading"><span class="eyebrow">添加联系人 / 接入</span><button id="connClose" aria-label="关闭接入引导">×</button></div><ol class="connection-steps">' + steps.map((s, i) => '<li class="' + (i + 1 === w.step ? 'active' : i + 1 < w.step ? 'done' : '') + '"><span>' + (i + 1) + '</span>' + s + '</li>').join('') + '</ol>' + content + '<p id="connError" role="alert"></p></div>');
  document.getElementById('connClose').onclick = hideModal;
}
function renderConnectionWizard() {
  const w = connectionWizard; if (!w) return;
  clearInterval(w.timer);
  if (w.step === 1) {
    wizardFrame(w, '<h3>谁来一起合拍？</h3><p class="hint">邀请朋友，连接 Harness。无论来自哪台设备，都在同一场会话里协作。</p><div class="connection-choices">' + Object.entries(CONNECTION_TYPES).map(([id, t]) => '<button class="connection-choice" data-connection-type="' + id + '"><span class="connection-symbol">' + t[2] + '</span><span><b>' + t[0] + '</b><small>' + t[1] + '</small></span><span>›</span></button>').join('') + '</div>');
    mbox.querySelectorAll('[data-connection-type]').forEach(b => b.onclick = () => { w.type = b.dataset.connectionType; w.base = connectionDefaultBase(w.type, w.info, location.origin); w.step = 2; renderConnectionWizard(); });
  } else if (w.step === 2) renderConnectionSetup(w);
  else if (w.step === 3) renderConnectionAssignment(w);
  else renderConnectionComplete(w);
}
function connectionNameFields(w) {
  if (w.saved) return '<div class="connection-identity"><b>' + esc((connectionContact(w) || {}).label || w.id) + '</b><small>@' + esc(w.id) + '</small></div>';
  return '<label>联系人名称<input id="connName" maxlength="40" placeholder="例如：我的 Harness" value="' + esc(w.form.label || '') + '"></label><details class="connection-advanced"><summary>高级：自定义联系人 ID</summary><input id="connId" maxlength="24" placeholder="留空自动生成，小写英文和数字" value="' + esc(w.form.id || '') + '"></details>';
}
function renderConnectionSetup(w) {
  let content = '<h3>' + CONNECTION_TYPES[w.type][0] + '</h3>';
  if (w.type === 'mydevice') {
    content += '<p class="hint">接入码 10 分钟内有效，只能使用一次。设备拿到长期凭证后，重启应用也不用重新接入。</p>'
      + '<label>设备名称<input id="connDeviceName" maxlength="40" placeholder="例如：我的 iPhone" value="' + esc(w.form.deviceName || '') + '"></label>'
      + '<label>接入地址<select id="connDeviceBase"></select></label><input id="connDeviceCustomBase" type="url" placeholder="https://example.ts.net" aria-label="自定义接入地址" hidden>'
      + '<p class="hint" id="connDeviceBaseHint"></p>'
      + '<button id="connDeviceCreate" class="pri">生成设备接入码</button>'
      + '<div id="connDeviceResult"></div>'
      + '<h4>已接入的设备</h4><div id="connDeviceList">正在读取…</div>';
  } else if (w.type === 'user') {
    w.target = w.target || ((ROOMS.find(r => !r.archived) || {}).id || '__new__');
    w.permissions = w.permissions || {read:true,post:true,trigger:true,board:false,files:false,work:false};
    content += '<p class="hint">邀请票不会预先创建联系人。对方打开链接后领取身份，只加入你选择的会话。</p>'
      + '<label>会话<select id="connGuestRoom">' + ROOMS.filter(r => !r.archived).map(r => '<option value="' + esc(r.id) + '"' + (w.target === r.id ? ' selected' : '') + '>' + esc(r.title) + '</option>').join('') + '<option value="__new__"' + (w.target === '__new__' ? ' selected' : '') + '>＋ 新建会话</option></select></label>'
      + '<div id="connGuestNew"' + (w.target === '__new__' ? '' : ' hidden') + '><label>新会话标题<input id="connGuestTitle" maxlength="60" placeholder="例如：与朋友协作"></label></div>'
      + '<h4>此会话内的权限</h4><div id="connPermissions" class="perm-grid">' + renderPermissionChoices(w) + '</div>'
      + '<details class="connection-advanced" open><summary>邀请地址（对方能打开的地址）</summary><label>接入地址<input id="connBase" value="' + esc(w.base) + '"></label><p class="hint" id="connBaseHint"></p></details>'
      + '<button id="connGuestGenerate" class="pri">生成邀请</button><div id="connGuestResult"></div>'
      + '<h4>邀请与游客</h4><div id="connInviteList">正在读取…</div>';
  } else if (w.type === 'local') {
    const local = CONTACTS.filter(a => ['cli', 'claude', 'codex'].includes(a.driver));
    w.localChoice = w.localChoice || (local.find(a => a.enabled) || {}).id || '__custom__';
    content += '<p class="hint">应用直接运行本机程序，不需要 MCP 地址或接入密钥。请先安装并登录你要使用的 Agent。</p><label>选择程序<select id="connLocal">' + local.map(a => '<option value="' + esc(a.id) + '"' + (w.localChoice === a.id ? ' selected' : '') + '>' + esc(a.label) + (a.enabled ? ' · 已检测到' : ' · 未找到程序') + '</option>').join('') + '<option value="__custom__"' + (w.localChoice === '__custom__' ? ' selected' : '') + '>自定义 CLI 程序…</option></select></label><div id="connCustom"' + (w.localChoice === '__custom__' ? '' : ' hidden') + '>' + connectionNameFields(w.localChoice === '__custom__' ? w : {...w, saved: false}) + '<label>命令或可执行文件路径<input id="connCommand" placeholder="my-agent 或完整程序路径" value="' + esc(w.form.command || '') + '"></label><label>调用参数（JSON 数组）<textarea id="connArgs">' + esc(w.form.args || '["-p", "{prompt}"]') + '</textarea></label></div><button id="connCheck" class="pri">检测并验证程序</button><p class="hint">仅运行 --version，不调用模型；登录和额度在首次回复时确认。</p>';
  } else {
    content += connectionNameFields(w);
    if (w.type === 'http') {
      content += w.saved ? '<p class="hint">使用此联系人已保存的回调地址和服务密钥。</p>' : '<label>Harness 回调地址<input id="connEndpoint" placeholder="http://127.0.0.1:9100/bridge" value="' + esc(w.form.endpoint || '') + '"></label><label>服务密钥（可选）<input id="connServiceKey" type="password" autocomplete="off" value="' + esc(w.form.serviceKey || '') + '"></label>';
      content += '<p class="hint">测试会向服务发送一条只需简短回复的验证消息，可能消耗少量模型额度，不授予工具或文件权限。</p><button id="connCheck" class="pri">测试连接与回复</button><details class="connection-advanced"><summary>查看服务需要实现的协议</summary><p>接收 POST JSON，其中包含 prompt、speaker、mode、permissions。返回 <code>{"text":"连接成功"}</code>。</p></details>';
    } else {
      if (w.type === 'external') content += '<label>参与方式<select id="connMemberMode"><option value="auto"'+(w.memberMode !== 'manual'?' selected':'')+'>自动接收任务并回复（推荐）</option><option value="manual"'+(w.memberMode === 'manual'?' selected':'')+'>通过 MCP 手动参与</option></select></label><div id="connManual"'+(w.memberMode === 'manual'?'':' hidden')+'><label>对方使用的软件<select id="connHost"><option value="custom">自定义 / 其他 MCP 客户端</option>' + ((agCat && agCat.hosts) || []).map(h => '<option value="' + esc(h.id) + '"' + (w.host === h.id ? ' selected' : '') + '>' + esc(h.label) + '</option>').join('') + '</select></label></div>';
      content += '<details class="connection-advanced" open><summary>接入地址（手机 / 其他设备用）</summary><label>对方能访问的应用地址<input id="connBase" value="' + esc(w.base) + '"></label><p class="hint" id="connBaseHint"></p></details><button id="connGenerate" class="pri">生成连接配置</button>' + (w.status && w.status.has_key ? '<button id="connRevoke">撤销现有密钥 / 邀请</button><p class="hint">已有密钥不会再次显示明文；重新生成会使旧密钥和旧邀请失效。</p>' : '') + '<div id="connCredentials"></div>';
    }
  }
  if (w.type === 'mydevice') {
    content += '<div class="btns connection-actions"><button id="connBack">更换接入方式</button><button id="connNext" class="pri">完成</button></div>';
  } else if (w.type === 'user') {
    content += '<div class="btns connection-actions"><button id="connBack">更换接入方式</button><button id="connNext" class="pri">完成</button></div>';
  } else {
    content += '<div id="connEvidence" class="connection-evidence" aria-live="polite"></div><div class="btns connection-actions"><button id="connBack">更换接入方式</button><button id="connLater">稍后验证，先分配会话</button><button id="connNext" class="pri" disabled>下一步：会话与权限</button></div>';
  }
  wizardFrame(w, content);
  document.getElementById('connBack').onclick = () => startConnectionWizard();
  if (document.getElementById('connLater')) document.getElementById('connLater').onclick = () => advanceConnection(w);
  document.getElementById('connNext').onclick = () => advanceConnection(w);
  if (w.type === 'mydevice') {
    renderDeviceSetup(w);
  }
  if (w.type === 'local') {
    const selected = connectionContact(w) || CONTACTS.find(a => a.id === w.localChoice);
    const help = document.createElement('p');
    help.id = 'connProgramHelp'; help.className = 'hint';
    help.textContent = selected && !selected.enabled ? '尚未找到 ' + selected.label + '。如果已安装，请重启应用以更新 PATH，或选择“自定义 CLI 程序”填写完整路径。也可先选择标有“已检测到”的程序。' : '';
    document.getElementById('connLocal').parentElement.after(help);
    document.getElementById('connLocal').onchange = async e => {
      if (w.busy) return;
      w.localChoice = e.target.value; w.id = w.localChoice === '__custom__' ? '' : w.localChoice;
      w.saved = !!w.id; w.status = (agCat.connections || {})[w.id] || null; w.form = {}; renderConnectionWizard();
    };
    if (w.localChoice !== '__custom__') { w.id = w.localChoice; w.saved = true; w.status = (agCat.connections || {})[w.id] || w.status; }
  }
  if (w.type === 'user') {
    renderGuestSetup(w);
  }
  const baseInput = document.getElementById('connBase');
  if (baseInput && w.type !== 'user') {
    const hint = document.getElementById('connBaseHint');
    const showBaseHint = () => {
      const issue = connectionBaseWarning(w.base);
      hint.textContent = issue || '这个地址必须能在朋友的电脑上打开。同一局域网可用局域网地址；跨网络需双方可达的 Tailscale 或 HTTPS 地址。HTTP 的消息和密钥未加密，避免在不可信网络使用。';
      hint.classList.toggle('warn', !!issue);
    };
    showBaseHint();
    baseInput.oninput = () => {
      w.base = baseInput.value.trim().replace(/\/+$/, '');
      showBaseHint();
      if (w.token) renderConnectionCredentials(w);
    };
  }
  const check = document.getElementById('connCheck'); if (check) check.onclick = () => checkConnection(w);
  const generate = document.getElementById('connGenerate'); if (generate) generate.onclick = () => generateConnection(w);
  const revoke = document.getElementById('connRevoke'); if (revoke) revoke.onclick = async () => {
    if (w.busy || !confirm('撤销后，此联系人的现有密钥和邀请立即失效，继续？')) return;
    setConnectionBusy(w, true);
    try { const [ok, j] = await post('/api/contacts/key', {id:w.id, revoke:true}); if (!ok) throw Error(j.error || '撤销失败'); if (wizardCurrent(w)) { w.token = ''; w.member = {automatic:false}; w.status = {state:'unconfigured', label:'密钥已撤销', has_key:false}; renderConnectionWizard(); } }
    catch (e) { if (wizardCurrent(w)) wizardError(e.message); }
    finally { if (wizardCurrent(w)) setConnectionBusy(w, false); }
  };
  const host = document.getElementById('connHost'); if (host) host.onchange = () => { w.host = host.value; if (w.token) renderConnectionCredentials(w); };
  const memberMode = document.getElementById('connMemberMode'); if (memberMode) memberMode.onchange = () => { w.memberMode = memberMode.value; document.getElementById('connManual').hidden = w.memberMode !== 'manual'; if (w.token) renderConnectionCredentials(w); };
  if (w.token) renderConnectionCredentials(w);
  updateConnectionEvidence(w);
  if (w.type === 'external' && w.id) w.timer = setInterval(() => pollConnectionEvidence(w), 2000);
}
function captureConnectionForm(w) {
  const value = id => { const e = document.getElementById(id); return e ? e.value.trim() : undefined; };
  for (const [field, id] of Object.entries({label:'connName', id:'connId', command:'connCommand', args:'connArgs', endpoint:'connEndpoint', serviceKey:'connServiceKey', deviceName:'connDeviceName'})) {
    const v = value(id); if (v !== undefined) w.form[field] = v;
  }
  if (document.getElementById('connBase')) w.base = value('connBase').replace(/\/+$/, '');
  if (document.getElementById('connHost')) w.host = value('connHost');
}
async function saveConnectionContact(w) {
  captureConnectionForm(w);
  if (w.saved) return;
  if (!w.form.label) throw Error('请填写联系人名称');
  const prefix = w.type === 'user' ? 'person' : w.type === 'local' ? 'agent' : 'harness';
  const id = w.form.id || prefix + '-' + crypto.getRandomValues(new Uint32Array(1))[0].toString(36);
  if (CONTACTS.some(a => a.id === id)) throw Error('此联系人 ID 已存在，请换一个或从联系人列表继续接入');
  const spec = {id, label: w.form.label, kind: w.type === 'user' ? 'user' : 'agent', driver: ['user','external'].includes(w.type) ? 'external' : w.type === 'local' ? 'cli' : 'http', enabled: true};
  if (w.type === 'local') {
    if (!w.form.command) throw Error('请填写程序命令或路径');
    let args; try { args = JSON.parse(w.form.args); } catch (e) { throw Error('调用参数应是 JSON 字符串数组'); }
    if (!Array.isArray(args) || !args.every(x => typeof x === 'string')) throw Error('调用参数应是 JSON 字符串数组');
    spec.bin = [w.form.command]; spec.args = {base: args}; spec.context = 'full'; delete spec.enabled;
  }
  if (w.type === 'http') { const u = new URL(w.form.endpoint); if (!['http:','https:'].includes(u.protocol)) throw Error('回调地址必须以 http:// 或 https:// 开头'); spec.endpoint = w.form.endpoint; spec.token = w.form.serviceKey || ''; }
  const [ok, j] = await post('/api/agents/add', {spec}); if (!ok) throw Error(j.error || '联系人保存失败');
  w.id = id; w.saved = true; await refreshContacts();
}
function setConnectionBusy(w, busy) {
  w.busy = busy;
  if (!wizardCurrent(w)) return;
  mbox.querySelectorAll('button,input,select,textarea').forEach(e => { if (e.id !== 'connClose') e.disabled = busy; });
  if (!busy) updateConnectionEvidence(w);
  const assign = document.getElementById('connAssign'); if (assign) assign.disabled = busy || !!w.loadingAssignment;
}
async function checkConnection(w) {
  if (w.busy) return; wizardError(''); setConnectionBusy(w, true);
  try {
    await saveConnectionContact(w); if (!wizardCurrent(w)) return;
    const box = document.getElementById('connEvidence'); box.textContent = w.type === 'http' ? '正在等待服务回复…' : '正在验证程序能否启动…';
    const [ok, j] = await post('/api/contacts/check', {id: w.id}); if (!ok) throw Error(j.error || '验证失败');
    if (!wizardCurrent(w)) return; w.status = j.connection;
    await refreshContacts(); if (!wizardCurrent(w)) return;
  } catch (e) { if (wizardCurrent(w)) wizardError(e.message || '验证失败，请重试'); }
  finally { if (wizardCurrent(w)) { setConnectionBusy(w, false); updateConnectionEvidence(w); } }
}
function updateConnectionEvidence(w) {
  const box = document.getElementById('connEvidence'); if (!box) return;
  let status = w.status || {state: 'unchecked', label: ['external','user'].includes(w.type) ? '等待生成连接配置' : '尚未验证'};
  const member = w.member || (agCat.members || {})[w.id];
  if (w.type === 'external' && member && member.automatic) status = {state:'connected', label:'自动响应已连接', detail:'成员设备：'+member.device+'。被 @ 后会自动处理并回复原会话。'};
  else if (w.type === 'external' && status.state === 'connected') status = {...status, label:'消息通道已连接', detail:'可以手动参与；开启成员连接端后，才能自动响应会议室任务。'};
  const good = ['ready','connected'].includes(status.state) && !(w.type === 'external' && w.memberMode !== 'manual' && !(member && member.automatic));
  box.className = 'connection-evidence ' + (good ? 'success' : status.state === 'failed' ? 'failure' : 'pending');
  box.innerHTML = '<b>' + (good ? '✓ ' : '○ ') + esc(status.label) + '</b><p>' + esc(status.detail || (status.state === 'connected' ? '已收到对方的认证连接' + (status.client ? ' · ' + status.client : '') : ['external','user'].includes(w.type) ? '配置或复制邀请后仍需对方实际打开连接。此处会自动更新，无需手动确认。' : '验证结果会显示在这里。')) + '</p>';
  const next = document.getElementById('connNext');
  if (next) next.disabled = w.busy || (!['user', 'mydevice'].includes(w.type) && !good);
}
async function pollConnectionEvidence(w) {
  if (!wizardCurrent(w) || w.step !== 2) { clearInterval(w.timer); return; }
  if (w.polling || w.busy) return; w.polling = true;
  try { const [ok, j] = await post('/api/contacts/status', {id: w.id}); if (ok && wizardCurrent(w)) { w.status = j.connection; w.member = j.member; updateConnectionEvidence(w); } }
  catch (e) { if (wizardCurrent(w)) { w.status = {...w.status, state:'offline', label:'暂时无法获取连接状态'}; updateConnectionEvidence(w); } }
  finally { w.polling = false; }
}
async function generateConnection(w) {
  if (w.busy) return; wizardError(''); captureConnectionForm(w);
  try { const u = new URL(w.base); if (!['http:','https:'].includes(u.protocol) || u.username || u.password || u.search || u.hash || u.pathname !== '/') throw 0; }
  catch (e) { wizardError('请填写应用根地址，例如 http://127.0.0.1:8765 或 https://bridge.example.com'); return; }
  if ((w.status && w.status.has_key || w.token) && !confirm('重新生成会使这个联系人的旧密钥和邀请立即失效，继续？')) return;
  setConnectionBusy(w, true);
  try {
    await saveConnectionContact(w); if (!wizardCurrent(w)) return;
    const [ok, j] = await post('/api/contacts/key', {id: w.id}); if (!ok) throw Error(j.error || '生成失败');
    if (!wizardCurrent(w)) return;
    w.token = j.token; w.member = {automatic:false}; w.status = {state: 'waiting', label: '等待对方连接', has_key: true};
    renderConnectionWizard();
  } catch (e) { if (wizardCurrent(w)) wizardError(e.message || '生成失败'); }
  finally { if (wizardCurrent(w)) setConnectionBusy(w, false); }
}
function connectionGuestUrl(base, token) {
  return base.replace(/\/+$/, '') + '/join#invite=' + encodeURIComponent(token);
}
function connectionInviteStatus(invite) {
  if (!invite || invite.revoked) return '已撤销';
  if (!invite.expires || invite.expires * 1000 < Date.now()) return '已过期';
  if (invite.guest_count) return '已加入 ' + invite.guest_count + ' 位游客';
  return '等待加入';
}
async function renderGuestSetup(w) {
  const select = document.getElementById('connGuestRoom');
  const newBox = document.getElementById('connGuestNew');
  if (select) select.onchange = () => {
    w.target = select.value;
    newBox.hidden = w.target !== '__new__';
  };
  const baseInput = document.getElementById('connBase');
  const hint = document.getElementById('connBaseHint');
  const showHint = () => {
    const issue = connectionBaseWarning(w.base);
    hint.textContent = issue || '游客的手机/电脑要能打开这个地址。跨网络需要你提供双方可达的地址。';
    hint.classList.toggle('warn', !!issue);
  };
  if (baseInput) {
    showHint();
    baseInput.oninput = () => { w.base = baseInput.value.trim().replace(/\/+$/, ''); showHint(); };
  }
  const generate = document.getElementById('connGuestGenerate');
  if (generate) generate.onclick = () => generateGuestInvite(w);
  if (w.inviteUrl) renderGuestInviteResult(w);
  await loadGuestInvites(w);
  if (wizardCurrent(w) && w.step === 2) w.timer = setInterval(() => pollGuestInvites(w), 3000);
}
async function loadGuestInvites(w) {
  try {
    const [ok, data] = await connectionGet('/api/invites');
    if (!ok) throw Error(data.error || '读取邀请失败');
    w.invites = data.invites || [];
    renderGuestInviteList(w);
  } catch (error) {
    const box = document.getElementById('connInviteList');
    if (box) box.textContent = error.message;
  }
}
function renderGuestInviteResult(w) {
  const box = document.getElementById('connGuestResult');
  if (!box) return;
  const issue = connectionBaseWarning(w.base);
  box.innerHTML = (issue ? '<p class="hint connection-base-warning">' + esc(issue) + ' 先换一个对方能打开的地址。</p>' : '')
    + '<input id="connGuestUrl" readonly aria-label="邀请链接"><button id="connGuestCopy">复制邀请链接</button>'
    + (issue ? '' : '<div id="connGuestQr" class="connection-qr" aria-label="邀请二维码"></div>');
  document.getElementById('connGuestUrl').value = w.inviteUrl;
  document.getElementById('connGuestCopy').onclick = async event => { await copyText(w.inviteUrl); event.target.textContent = '已复制'; };
  if (!issue) renderConnectionQrUrl('connGuestQr', w.inviteUrl);
}
function renderGuestInviteList(w) {
  const box = document.getElementById('connInviteList');
  if (!box) return;
  box.innerHTML = (w.invites || []).map(invite => {
    const room = ROOMS.find(r => r.id === invite.room);
    return '<div class="connection-invite"><span><b>' + esc((room || {}).title || invite.room) + '</b><small>'
      + esc(connectionInviteStatus(invite)) + ' · 创建于 ' + new Date((invite.created || 0) * 1000).toLocaleString()
      + ' · 有效至 ' + new Date((invite.expires || 0) * 1000).toLocaleString() + '</small>'
      + (invite.guests || []).map(guest => '<small class="connection-guest">' + esc(guest.label) + ' · ' + esc(guest.device || '设备未知') + (guest.online ? ' · 在线' : '') + ' <button data-guest-remove="' + esc(guest.id) + '">移除</button></small>').join('')
      + '</span>' + (invite.revoked ? '' : '<button data-invite-revoke="' + esc(invite.id) + '">撤销邀请</button>') + '</div>';
  }).join('') || '<p class="hint">还没有邀请。生成后会出现在这里。</p>';
  box.querySelectorAll('[data-invite-revoke]').forEach(button => {
    button.onclick = () => revokeGuestInvite(w, button.dataset.inviteRevoke);
  });
  box.querySelectorAll('[data-guest-remove]').forEach(button => {
    button.onclick = () => removeGuest(w, button.dataset.guestRemove);
  });
}
async function pollGuestInvites(w) {
  if (!wizardCurrent(w) || w.step !== 2) { clearInterval(w.timer); return; }
  await loadGuestInvites(w);
}
async function generateGuestInvite(w) {
  if (w.busy) return;
  wizardError('');
  w.permissions = Object.fromEntries([...mbox.querySelectorAll('[data-connection-permission]')].map(i => [i.dataset.connectionPermission, i.checked]));
  const title = document.getElementById('connGuestTitle');
  setConnectionBusy(w, true);
  try {
    if (w.target === '__new__') {
      const [ok, created] = await post('/api/rooms/new', {
        project: (PROJECTS[0] || {}).id || '', title: (title && title.value.trim()) || '与朋友协作', participants: []
      });
      if (!ok) throw Error(created.error || '会话创建失败');
      w.target = created.id;
      await pollRooms();
    }
    const [ok, data] = await post('/api/invites', {
      room: w.target, permissions: w.permissions, label: '游客邀请', expires_days: 7, base: w.base
    });
    if (!ok) throw Error(data.error || '邀请生成失败');
    w.inviteUrl = data.url || connectionGuestUrl(w.base, data.token);
    w.step = 2;
    renderConnectionWizard();
  } catch (error) {
    if (wizardCurrent(w)) wizardError(error.message);
  } finally {
    if (wizardCurrent(w)) setConnectionBusy(w, false);
  }
}
async function revokeGuestInvite(w, id) {
  if (!confirm('撤销后，这张邀请不能再兑换；已加入的游客不受影响。继续？')) return;
  const [ok, data] = await post('/api/invites/revoke', {id});
  if (!ok) return wizardError(data.error || '撤销失败');
  await loadGuestInvites(w);
}
async function removeGuest(w, id) {
  if (!confirm('移除后，这位游客会退出所有会话，密钥立即失效。继续？')) return;
  const [ok, data] = await post('/api/guests/remove', {id});
  if (!ok) return wizardError(data.error || '移除失败');
  await loadGuestInvites(w);
}
function connectionDeviceName(value) { return (value || '').trim() || '我的设备'; }
function connectionDeviceUrl(base, code) { return base.replace(/\/+$/, '') + '/enroll#code=' + encodeURIComponent(code); }
function connectionConfiguration(w) {
  if (w.host === 'codex') return '[mcp_servers.ai-bridge]\nurl = ' + JSON.stringify(w.base + '/mcp') + '\nhttp_headers = { Authorization = ' + JSON.stringify('Bearer ' + w.token) + ' }';
  return JSON.stringify({mcpServers: {'ai-bridge': {url: w.base + '/mcp', headers: {Authorization: 'Bearer ' + w.token}}}}, null, 2);
}
function renderConnectionCredentials(w) {
  const box = document.getElementById('connCredentials'); if (!box || !w.token) return;
  if (w.memberMode !== 'manual') {
    box.innerHTML = '<ol class="connection-instructions"><li>下载下方的成员连接端和连接配置，通过可信渠道把两个文件发给朋友。</li><li>朋友解压连接端：Windows 双击 start-member.bat；Mac 双击 start-member.command（需要 Python 3.11 和 Tk）。</li><li>朋友导入连接配置，选择自己电脑上的 Harness 和工作文件夹，点击「连接并开启自动响应」。</li><li>这里显示「自动响应已连接」后，选择会话与权限。成员随后能接收任务、回复和互相 @。</li></ol><p><a href="/static/member-connector.zip" download="Cojam-Member.zip">下载成员连接端（Windows / macOS 源码包）</a></p><button id="connDownloadMember" class="pri">下载此成员的连接配置</button><button id="connCopyMember">复制连接配置</button><p class="hint">连接配置含成员密钥，只发给对应朋友，不要公开。跨网络需要朋友能够访问上面的地址；不会自动组网，也无需开放朋友电脑的入站端口。</p>';
    const profile = JSON.stringify({protocol:1, server:w.base, key:w.token, member:w.id},null,2);
    document.getElementById('connCopyMember').onclick = () => copyText(profile);
    document.getElementById('connDownloadMember').onclick = () => { const url=URL.createObjectURL(new Blob([profile],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='ai-bridge-'+w.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); };
  } else {
    const host = ((agCat && agCat.hosts) || []).find(h => h.id === w.host);
    box.innerHTML = '<ol class="connection-instructions"><li>打开' + esc(host ? host.label : '对方 Harness') + '的 MCP 设置。</li><li>复制下面的配置并保存，然后重新加载该软件的 MCP 连接。</li><li>保持本页打开；收到对方的认证请求后会显示「已连接」。</li></ol><pre id="connMcpConfig"></pre><button id="connCopyConfig">复制连接配置</button>' + (host && host.present && w.base === location.origin ? '<button id="connInstall">一键写入本机 ' + esc(host.label) + ' 配置</button><p class="hint">会先备份原配置。写入后仍需在该软件中重新加载连接。</p>' : '') + '<details class="connection-advanced"><summary>自定义 HTTP 接入 / 手动填写密钥</summary><p>请求头：Authorization: Bearer &lt;密钥&gt;</p><input id="connRawKey" readonly aria-label="联系人接入密钥"><button id="connCopyKey">复制密钥</button><pre id="connHttpExample"></pre></details>';
    document.getElementById('connMcpConfig').textContent = connectionConfiguration(w);
    document.getElementById('connRawKey').value = w.token;
    document.getElementById('connHttpExample').textContent = 'GET ' + w.base + '/api/external/rooms\nGET ' + w.base + '/api/external/messages?room=<会话 ID>&since=0\nPOST ' + w.base + '/api/post\n{"room":"会话 ID","text":"消息"}\n\n先请求 /api/external/rooms 即可确认连接；受邀会话会在分配后出现。';
    document.getElementById('connCopyConfig').onclick = async e => { await copyText(connectionConfiguration(w)); e.target.textContent = '配置已复制'; };
    document.getElementById('connCopyKey').onclick = () => copyText(w.token);
    const install = document.getElementById('connInstall'); if (install) install.onclick = async () => {
      install.disabled = true;
      try { const [ok, j] = await post('/api/agents/install', {host: w.host, contact: w.id, token: w.token}); if (!ok) throw Error(j.error || '配置失败'); wizardError(''); install.textContent = '配置已写入，请在对方软件重新加载'; }
      catch (e) { wizardError(e.message); install.disabled = false; }
    };
  }
}
async function advanceConnection(w) {
  if (w.type === 'user') { hideModal(); refreshContacts(); loadState(); return; }
  if (w.type === 'mydevice') {
    wizardError('');
    w.step = 4;
    renderConnectionWizard();
    return;
  }
  if (w.busy) return; wizardError(''); setConnectionBusy(w, true);
  try {
    await saveConnectionContact(w); await pollRooms(); if (!wizardCurrent(w)) return;
    w.target = w.target || RID || (ROOMS.find(r => !r.archived) || {}).id || '__new__';
    w.project = w.project || (PROJECTS[0] || {}).id || '__new__'; w.step = 3; renderConnectionWizard();
  } catch (e) { if (wizardCurrent(w)) wizardError(e.message); }
  finally { if (wizardCurrent(w)) setConnectionBusy(w, false); }
}
function renderPermissionChoices(w) {
  return Object.entries(PERM_LABELS).map(([key, label]) => '<label><input type="checkbox" data-connection-permission="' + key + '"' + (w.permissions[key] ? ' checked' : '') + '>' + label + '</label>').join('');
}
function renderConnectionAssignment(w) {
  const contact = connectionContact(w) || {label: w.id};
  wizardFrame(w, '<h3>让' + esc(contact.label) + '加入哪个会话？</h3><p class="hint">该联系人只会加入你选择的会话。其他会话的成员和权限保持独立。</p><label>会话<select id="connRoom">' + ROOMS.filter(r => !r.archived || r.id === w.target).map(r => '<option value="' + esc(r.id) + '"' + (w.target === r.id ? ' selected' : '') + '>' + esc(((PROJECTS.find(p => p.id === r.project) || {}).name || '项目') + ' / ' + r.title) + '</option>').join('') + '<option value="__new__"' + (w.target === '__new__' ? ' selected' : '') + '>＋ 新建会话</option></select></label><div id="connNewRoom"' + (w.target === '__new__' ? '' : ' hidden') + '><label>所属项目<select id="connProject">' + PROJECTS.map(p => '<option value="' + esc(p.id) + '"' + (w.project === p.id ? ' selected' : '') + '>' + esc(p.name) + '</option>').join('') + '<option value="__new__"' + (w.project === '__new__' ? ' selected' : '') + '>＋ 新建项目</option></select></label><div id="connNewProject"' + (w.project === '__new__' ? '' : ' hidden') + '><label>项目名称<input id="connProjectName" maxlength="60" placeholder="例如：我的项目"></label><label>项目工作文件夹<input id="connWorkdir" placeholder="已存在的文件夹完整路径" value="' + esc((agCat && agCat.default_dir) || '') + '"></label></div><label>会话标题<input id="connRoomTitle" maxlength="60" placeholder="例如：与 ' + esc(contact.label) + ' 协作"></label></div><h4>此会话内的权限</h4><div id="connPermissions" class="perm-grid">' + renderPermissionChoices(w) + '</div><p class="hint">干活还需会话切换到干活模式。CLI / 外部 Harness 的文件与命令权限由其自身环境落实。</p><div class="btns connection-actions"><button id="connAssignmentBack">返回连接设置</button><button id="connSaveOnly">仅保留联系人，暂不加入</button><button id="connAssign" class="pri">保存并加入会话</button></div>');
  attachWorkspaceFolderPicker('connWorkdir');
  document.getElementById('connRoom').onchange = () => updateConnectionAssignment(w);
  document.getElementById('connProject').onchange = e => { w.project = e.target.value; document.getElementById('connNewProject').hidden = w.project !== '__new__'; };
  document.getElementById('connAssignmentBack').onclick = () => { w.step = 2; renderConnectionWizard(); };
  document.getElementById('connSaveOnly').onclick = () => { w.assigned = false; w.step = 4; renderConnectionWizard(); };
  document.getElementById('connAssign').onclick = () => assignConnection(w);
  updateConnectionAssignment(w);
}
async function updateConnectionAssignment(w) {
  const seq = (w.assignmentSeq || 0) + 1; w.assignmentSeq = seq;
  w.target = document.getElementById('connRoom').value;
  document.getElementById('connNewRoom').hidden = w.target !== '__new__';
  if (w.target === '__new__') { w.loadingAssignment = false; w.permissions = {read:true,post:true,trigger:true,board:false,files:false,work:false}; document.getElementById('connPermissions').innerHTML = renderPermissionChoices(w); document.getElementById('connAssign').disabled = w.busy; return; }
  w.loadingAssignment = true;
  const save = document.getElementById('connAssign'); save.disabled = true;
  try {
    const r = await fetch('/api/r/' + encodeURIComponent(w.target) + '/state'); const state = await r.json(); if (!r.ok) throw Error(state.error || '读取会话失败');
    if (!wizardCurrent(w) || w.assignmentSeq !== seq || w.step !== 3) return;
    w.permissions = (state.permissions || {})[w.id] || {read:true,post:true,trigger:true,board:false,files:false,work:false};
    w.loadingAssignment = false; document.getElementById('connPermissions').innerHTML = renderPermissionChoices(w); save.disabled = w.busy;
  } catch (e) { if (wizardCurrent(w) && w.assignmentSeq === seq) wizardError(e.message); }
}
async function assignConnection(w) {
  if (w.busy) return;
  w.permissions = Object.fromEntries([...mbox.querySelectorAll('[data-connection-permission]')].map(i => [i.dataset.connectionPermission, i.checked]));
  const roomTitle = document.getElementById('connRoomTitle').value.trim() || '与' + (connectionContact(w) || {}).label + '协作';
  const projectName = document.getElementById('connProjectName').value.trim(), workdir = document.getElementById('connWorkdir').value.trim();
  setConnectionBusy(w, true); wizardError('');
  try {
    if (w.target === '__new__') {
      if (w.project === '__new__') {
        const [ok, j] = await post('/api/projects/new', {name: projectName, workdir}); if (!ok) throw Error(j.error || '项目创建失败');
        w.project = j.project.id;
        if (!wizardCurrent(w)) return;
      }
      const [ok, j] = await post('/api/rooms/new', {project: w.project, title: roomTitle, participants: []}); if (!ok) throw Error(j.error || '会话创建失败');
      w.target = j.id;
      if (!wizardCurrent(w)) return;
      if (wizardCurrent(w)) { const select = document.getElementById('connRoom'); select.add(new Option(roomTitle, j.id)); select.value = j.id; }
    }
    const [ok, j] = await post('/api/r/' + encodeURIComponent(w.target) + '/invite', {contact: w.id, permissions: w.permissions}); if (!ok) throw Error(j.error || '邀请失败');
    await pollRooms(); await refreshContacts(); if (!wizardCurrent(w)) return;
    w.assigned = true; w.status = (agCat.connections || {})[w.id] || w.status; w.step = 4; renderConnectionWizard();
  } catch (e) { if (wizardCurrent(w)) wizardError(e.message); }
  finally { if (wizardCurrent(w)) setConnectionBusy(w, false); }
}
function renderConnectionComplete(w) {
  if (w.type === 'mydevice') {
    wizardFrame(w, '<div class="connection-complete"><span class="connection-symbol">✓</span><h3>我的设备</h3>'
      + '<p class="hint">在要接入的设备上打开接入码链接。接入后，这台设备会获得完整界面，并以「我」的身份参与会话。</p>'
      + ((w.devices || []).length ? '<p class="hint">已接入 ' + w.devices.length + ' 台设备。可随时在接入页撤销。</p>' : '')
      + '</div><div class="btns"><button id="connDone">完成</button></div>');
    document.getElementById('connDone').onclick = () => { hideModal(); refreshContacts(); loadState(); };
    return;
  }
  const contact = connectionContact(w) || {label:w.id}, room = ROOMS.find(r => r.id === w.target);
  const automatic = w.type === 'external' && ((w.member || (agCat.members||{})[w.id] || {}).automatic);
  const good = automatic || w.status && ['ready','connected'].includes(w.status.state);
  const connectionLabel = automatic ? '自动响应已连接：可与本机成员一样接收任务并回复' : w.type === 'external' ? '成员已保存。消息通道与自动响应状态可在联系人中查看。' : good ? w.status.label : '联系人已保存，尚未完成连接验证。可以稍后从联系人列表继续接入。';
  wizardFrame(w, '<div class="connection-complete"><span class="connection-symbol">✓</span><h3>' + (w.assigned ? '已加入会话' : '联系人已添加') + '</h3><p><b>' + esc(contact.label) + '</b>' + (w.assigned ? ' · ' + esc((room || {}).title || '') : '') + '</p><p class="hint">' + esc(connectionLabel) + '</p>' + (w.assigned ? '<p class="hint">权限：' + Object.entries(PERM_LABELS).filter(([k]) => w.permissions[k]).map(([,label]) => esc(label)).join('、') + '</p>' : '') + '</div><div class="btns"><button id="connDone">完成</button>' + (w.assigned ? '<button id="connEnter" class="pri">进入会话</button>' : '') + '</div>');
  document.getElementById('connDone').onclick = () => { hideModal(); refreshContacts(); loadState(); };
  const enter = document.getElementById('connEnter'); if (enter) enter.onclick = () => { const rid = w.target; hideModal(); switchRoom(rid); };
}
