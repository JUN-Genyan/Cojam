// Room-specific model controls. A blank value inherits the room/agent default.
const EFFORT_LABELS = {none:'关闭', minimal:'最低', low:'低', medium:'中', high:'高', xhigh:'很高', max:'最高'};
function modelEfforts(info, model) {
  const key = model || info.default_model || '';
  const catalog = info.efforts_by_model || {};
  return Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : (info.efforts || []);
}
function modelSettingsMarkup(info, busy) {
  const supported = info.model_supported !== false;
  const models = info.models || [], custom = !!info.model && !models.includes(info.model);
  return '<section id="modelSettings"><h3>模型与思考强度</h3><p class="hint" id="mCurrent">当前设置：'
    + esc(info.label || '由 Agent 决定') + ' · 仅此会话</p>'
    + '<p class="hint">'+esc(info.execution && info.execution.confirmed ? '最近一次执行端报告：'+info.execution.reported : '当前显示已选择的设置；实际模型等待执行端报告。')+'</p>'
    + '<p class="hint">' + esc(info.note || '') + '</p>'
    + '<p class="hint" id="mCatalogStatus" role="status">' + esc(info.catalog ? modelCatalogLabel(info.catalog) : '正在准备模型目录…') + '</p>'
    + '<div class="btns"><button type="button" id="mRefresh">刷新模型目录</button></div>'
    + (supported ? '<label>搜索模型<input id="mModelSearch" type="search" placeholder="名称或模型 ID"></label><label>模型<select id="mModelChoice"' + (busy ? ' disabled' : '') + '><option value="">'
      + esc(info.default_model ? '会话默认：' + info.default_model : '使用 Agent 默认模型') + '</option>'
      + models.map(value => '<option value="' + esc(value) + '"' + (value === info.model ? ' selected' : '') + '>' + esc((((info.catalog || {}).entries || []).find(row => row.id === value) || {}).label || value) + ' · ' + esc(value) + '</option>').join('')
      + '<option value="__custom__"' + (custom ? ' selected' : '') + '>自定义模型 ID…</option></select></label>'
      + '<label id="mCustomModel"' + (custom ? '' : ' hidden') + '>模型 ID<input class="mi" id="mModel" placeholder="填写此 Agent 支持的模型 ID" value="' + esc(info.model || '')
      + '"' + (busy ? ' disabled' : '') + '></label>' : '')
    + (supported || info.effort_supported
      ? '<label>思考强度<select id="mEffort"' + (busy ? ' disabled' : '') + '></select></label><p class="hint" id="mEffortHint"></p>'
      : '<p class="hint">思考强度由 Agent 管理。</p>')
    + '<div id="mReasoningExtra"></div>'
    + (supported || info.effort_supported ? '<div class="btns"><button type="button" class="pri" id="mSave" disabled>保存设置</button><button type="button" id="mReset"' + (busy ? ' disabled' : '') + '>恢复默认</button></div>' : '')
    + '<p class="hint" id="mHint" role="status" aria-live="polite">' + (busy ? '有 AI 正在回复或排队，结束后重新打开即可修改。' : '保存后从下一条消息生效。') + '</p></section>';
}
function bindModelSettings(ai, info, busy, options = {}) {
  const box = document.getElementById('modelSettings');
  const model = document.getElementById('mModel'), effort = document.getElementById('mEffort');
  const choice = document.getElementById('mModelChoice'), custom = document.getElementById('mCustomModel');
  const save = document.getElementById('mSave'), reset = document.getElementById('mReset');
  const refresh = document.getElementById('mRefresh');
  if (refresh) refresh.onclick = () => options.refresh ? options.refresh() : loadModelCatalog(ai, busy, true);
  if (!box || !save) return;
  const rid = RID, url = api('models');
  let saving = false;
  const current = () => (options.isCurrent ? options.isCurrent() : RID === rid) && document.getElementById('modelSettings') === box;
  const hint = message => { if (current()) document.getElementById('mHint').textContent = message; };
  let selectedControls = info.reasoning || {};
  box.modelTouched = false;
  box.modelDraft = () => ({ai, model:model ? model.value.trim() : '', effort:effort ? effort.value : '', reasoning:selectedControls});
  const dirty = () => (model ? model.value.trim() : '') !== (info.model || '') || (effort ? effort.value : '') !== (info.effort || '') || JSON.stringify(selectedControls) !== JSON.stringify(info.reasoning || {});
  box.modelDirty = dirty;
  const update = () => { save.disabled = busy || saving || !dirty(); };
  const renderEfforts = value => {
    if (!effort) return;
    const choices = modelEfforts(info, model ? model.value.trim() : '');
    const inherited = choices.includes(info.default_effort) ? info.default_effort : '';
    effort.innerHTML = '<option value="">' + esc(inherited ? '会话默认（' + (EFFORT_LABELS[inherited] || inherited) + '）' : '由 Agent 决定') + '</option>'
      + choices.map(item => '<option value="' + esc(item) + '">' + esc((EFFORT_LABELS[item] || item) + ' · ' + item) + '</option>').join('');
    effort.value = choices.includes(value) ? value : '';
    effort.disabled = busy || saving || !choices.length;
    document.getElementById('mEffortHint').textContent = choices.length ? '选项随模型变化；更高的思考强度通常需要更长时间。' : '此模型未提供可选择的思考强度。';
    const extra = document.getElementById('mReasoningExtra');
    const entry = ((info.catalog || {}).entries || []).find(row => row.id === ((model && model.value.trim()) || info.default_model));
    const controls = entry && entry.reasoning;
    if (extra) {
      extra.innerHTML = '';
      if (controls && ['budget','toggle'].includes(controls.kind)) {
        effort.closest('label').hidden = true;
        const saved = selectedControls.kind === controls.kind ? selectedControls.value : '';
        extra.innerHTML = controls.kind === 'budget'
          ? '<label>思考 token 预算<input id="mBudget" type="number" min="'+controls.min+'" max="'+controls.max+'" placeholder="由 Agent 决定" value="'+esc(saved)+'"'+(busy?' disabled':'')+'></label>'
          : '<label>思考模式<select id="mThinking"'+(busy?' disabled':'')+'><option value="">由 Agent 决定</option><option value="true"'+(saved===true?' selected':'')+'>开启</option><option value="false"'+(saved===false?' selected':'')+'>关闭</option></select></label>';
        const input = document.getElementById(controls.kind === 'budget' ? 'mBudget' : 'mThinking');
        input.oninput = input.onchange = () => { box.modelTouched = true; selectedControls = input.value === '' ? {} : {kind:controls.kind, value:controls.kind === 'budget' ? Number(input.value) : input.value === 'true'}; update(); };
      } else {
        if (typeof effort.closest === 'function') effort.closest('label').hidden = false;
        if (controls && selectedControls.kind && selectedControls.kind !== controls.kind) selectedControls = {};
      }
    }
    if (value && !choices.includes(value)) hint('原来的思考强度不适用于此模型，已改用默认；请保存设置。');
  };
  renderEfforts(info.effort || '');
  if (model) model.oninput = () => { box.modelTouched = true; selectedControls = {}; renderEfforts(effort ? effort.value : ''); update(); };
  if (choice) choice.onchange = () => {
    custom.hidden = choice.value !== '__custom__';
    model.value = custom.hidden ? choice.value : '';
    model.oninput();
    if (!custom.hidden) model.focus();
  };
  if (effort) effort.onchange = () => { box.modelTouched = true; selectedControls = {}; update(); };
  reset.onclick = () => { box.modelTouched = true; selectedControls = {}; if (model) model.value = ''; if (choice) { choice.value = ''; custom.hidden = true; } renderEfforts(''); hint('已选择默认设置，保存后生效。'); update(); };
  save.onclick = async () => {
    if (busy || saving || !dirty()) return;
    const selectedModel = model ? model.value.trim() : '', selectedEffort = effort ? effort.value : '';
    saving = true; update(); reset.disabled = true; if (choice) choice.disabled = true; if (model) model.disabled = true; if (effort) effort.disabled = true;
    hint('正在保存…');
    try {
      const body = {ai, model:selectedModel, effort:selectedEffort};
      if (info.catalog) body.reasoning = selectedControls;
      const [ok, data] = options.onSave ? await options.onSave(body) : await post(url, body);
      if (!ok) throw Error(data.error || '保存失败，请重试');
      info.model = selectedModel; info.effort = selectedEffort; info.reasoning = selectedControls;
      box.modelTouched = false;
      hint(options.savedMessage || '已保存，下一条消息使用新设置。');
      if (current()) {
        try { if (!options.onSave) await loadState(); }
        catch (error) { hint('设置已保存，暂时无法刷新会话状态。'); }
        if (current()) {
          const latest = S && S.ais && S.ais[ai] && S.ais[ai].model;
          if (latest) document.getElementById('mCurrent').textContent = '当前设置：' + latest.label + ' · 仅此会话';
        }
      }
    } catch (error) { hint(error.message || '保存失败，请检查连接后重试。'); }
    finally {
      saving = false;
      if (current()) { reset.disabled = busy; if (choice) choice.disabled = busy; if (model) model.disabled = busy; if (effort) effort.disabled = busy || !modelEfforts(info, model ? model.value.trim() : '').length; update(); }
    }
  };
  update();
  const search = document.getElementById('mModelSearch');
  if (search && choice) search.oninput = () => {
    const needle = search.value.toLowerCase();
    const selected = choice.value;
    const entries = (info.models || []).map(id => ({id,label:(((info.catalog || {}).entries || []).find(row=>row.id===id)||{}).label || id}));
    choice.innerHTML = '<option value="">使用默认模型</option>' + entries.filter(row=>row.id===selected || (row.id+' '+row.label).toLowerCase().includes(needle))
      .map(row=>'<option value="'+esc(row.id)+'">'+esc(row.label)+' · '+esc(row.id)+'</option>').join('')+'<option value="__custom__">自定义模型 ID…</option>';
    choice.value = selected;
  };
}

function modelCatalogLabel(catalog) {
  const sources = {account:'当前账户目录', cache:'本机缓存', configuration:'本机配置', preset:'官方候选', cli_candidates:'程序候选目录', http:'服务目录', member:'执行设备目录'};
  const state = {loading:'正在读取', ready:'已读取', candidate:'候选模型，权限待实际回复确认', login_required:'需要在原程序登录', unsupported:'此接入方式或版本不可控制', error:'目录读取失败', missing:'未找到可调用程序'};
  return (state[catalog.status] || '') + (catalog.source ? ' · ' + (sources[catalog.source] || catalog.source) : '') + (catalog.version ? ' · ' + catalog.version : '') + (catalog.stale ? ' · 上次结果已过期' : '') + (catalog.refreshing ? ' · 正在刷新' : '');
}
async function loadModelCatalog(ai, busy, refresh = false) {
  const rid = RID, box = document.getElementById('modelSettings');
  if (!box) return;
  const sequence = (box.catalogSequence || 0) + 1; box.catalogSequence = sequence;
  const same = () => RID === rid && document.getElementById('modelSettings') === box && box.catalogSequence === sequence;
  try {
    if (refresh) {
      const [ok, result] = await post(api('models/refresh'), {ai});
      if (!ok) throw Error(result.error || '刷新失败');
    }
    const response = await fetch(api('models/catalog') + '?ai=' + encodeURIComponent(ai));
    const info = await response.json();
    if (!response.ok) throw Error(info.error || '读取目录失败');
    if (!same()) return;
    const status = document.getElementById('mCatalogStatus');
    if (status) status.textContent = modelCatalogLabel(info.catalog || {});
    if (box.modelDirty && box.modelDirty() && box.modelTouched !== false) {
      if (status) status.textContent += '。已保留未保存设置，保存或恢复默认后重新打开可使用新目录。';
    } else if (!(info.catalog || {}).refreshing) {
      box.outerHTML = modelSettingsMarkup(info, busy);
      bindModelSettings(ai, info, busy);
      return;
    }
    if ((info.catalog || {}).refreshing) setTimeout(() => { if (same()) loadModelCatalog(ai, busy); }, 1500);
  } catch (error) {
    if (same()) { const status = document.getElementById('mCatalogStatus'); if (status) status.textContent = error.message || '读取目录失败，可点击刷新重试。'; }
  }
}
