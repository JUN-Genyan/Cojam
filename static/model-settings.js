// Room-specific model controls. A blank value inherits the room/agent default.
const EFFORT_LABELS = {none:'关闭', minimal:'最低', low:'低', medium:'中', high:'高', xhigh:'很高', max:'最高'};
function modelApplicationLabel(info) {
  const labels={pending:'已选择，待下一轮应用',applied:'执行端已接受设置',failed:'设置应用失败',read_only:'桌面只读回报'};
  let text=labels[info.apply_status] || '尚未确认应用';
  if(info.observed && info.observed.model)text+=' · 实际回报：'+info.observed.model+(info.observed.effort?' / '+info.observed.effort:'');
  if(info.apply_error)text+=' · '+info.apply_error;
  return text;
}
function modelCatalogLabel(info) {
  return '索引来源：'+(info.source || '接入配置')+(info.checked_at?' · '+new Date(info.checked_at*1000).toLocaleString():'')+(info.stale?' · 已过期，保留上次列表':'')+(info.error?' · '+info.error:'');
}
function modelEfforts(info, model) {
  const key = model || info.default_model || '';
  const catalog = info.efforts_by_model || {};
  return Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : (info.efforts || []);
}
function modelSettingsMarkup(info, busy) {
  const supported = info.model_supported !== false;
  const models = info.models || [], custom = !!info.model && !models.includes(info.model);
  return '<section id="modelSettings"><h3>模型与思考强度</h3><p class="hint" id="mCurrent">当前设置：'
    + esc(info.label || '模型未回报') + ' · 仅此会话</p>'
    + '<p class="hint">' + esc(info.note || '') + '</p>'
    + '<p class="hint" id="mApplyStatus">'+esc(modelApplicationLabel(info))+'</p><p class="hint" id="mCatalogStatus">'+esc(modelCatalogLabel(info))+'</p><button type="button" id="mRefreshCatalog"'+(busy?' disabled':'')+'>刷新模型索引</button>'
    + (supported ? '<label>模型<select id="mModelChoice"' + (busy ? ' disabled' : '') + '><option value="">'
      + esc(info.default_model ? '会话默认：' + info.default_model : '使用 Agent 默认模型') + '</option>'
      + models.map(value => '<option value="' + esc(value) + '"' + (value === info.model ? ' selected' : '') + '>' + esc(value) + '</option>').join('')
      + '<option value="__custom__"' + (custom ? ' selected' : '') + '>自定义模型 ID…</option></select></label>'
      + '<label id="mCustomModel"' + (custom ? '' : ' hidden') + '>模型 ID<input class="mi" id="mModel" placeholder="填写此 Agent 支持的模型 ID" value="' + esc(info.model || '')
      + '"' + (busy ? ' disabled' : '') + '></label>' : '')
    + (info.effort_supported !== false && (info.effort_supported || (info.efforts || []).length)
      ? '<label>思考强度<select id="mEffort"' + (busy ? ' disabled' : '') + '></select></label><p class="hint" id="mEffortHint"></p>'
      : '<p class="hint">当前接入未提供 effort 设置接口。</p>')
    + (supported || info.effort_supported ? '<div class="btns"><button type="button" class="pri" id="mSave" disabled>保存设置</button><button type="button" id="mReset"' + (busy ? ' disabled' : '') + '>恢复默认</button></div>' : '')
    + '<p class="hint" id="mHint" role="status" aria-live="polite">' + (busy ? '有 AI 正在回复或排队，结束后重新打开即可修改。' : '保存后从下一条消息生效。') + '</p></section>';
}
function bindModelSettings(ai, info, busy) {
  bindModelCatalogRefresh(ai,info,busy);
  const box = document.getElementById('modelSettings');
  const model = document.getElementById('mModel'), effort = document.getElementById('mEffort');
  const choice = document.getElementById('mModelChoice'), custom = document.getElementById('mCustomModel');
  const save = document.getElementById('mSave'), reset = document.getElementById('mReset');
  if (!box || !save) return;
  const rid = RID, url = api('models');
  let saving = false;
  const current = () => RID === rid && document.getElementById('modelSettings') === box;
  const hint = message => { if (current()) document.getElementById('mHint').textContent = message; };
  const dirty = () => (model ? model.value.trim() : '') !== (info.model || '') || (effort ? effort.value : '') !== (info.effort || '');
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
    if (value && !choices.includes(value)) hint('原来的思考强度不适用于此模型，已改用默认；请保存设置。');
  };
  renderEfforts(info.effort || '');
  if (model) model.oninput = () => { renderEfforts(effort ? effort.value : ''); update(); };
  if (choice) choice.onchange = () => {
    custom.hidden = choice.value !== '__custom__';
    model.value = custom.hidden ? choice.value : '';
    model.oninput();
    if (!custom.hidden) model.focus();
  };
  if (effort) effort.onchange = update;
  reset.onclick = () => { if (model) model.value = ''; if (choice) { choice.value = ''; custom.hidden = true; } renderEfforts(''); hint('已选择默认设置，保存后生效。'); update(); };
  save.onclick = async () => {
    if (busy || saving || !dirty()) return;
    const selectedModel = model ? model.value.trim() : '', selectedEffort = effort ? effort.value : '';
    saving = true; update(); reset.disabled = true; if (choice) choice.disabled = true; if (model) model.disabled = true; if (effort) effort.disabled = true;
    hint('正在保存…');
    try {
      const [ok, data] = await post(url, {ai, model:selectedModel, effort:selectedEffort});
      if (!ok) throw Error(data.error || '保存失败，请重试');
      info.model = selectedModel; info.effort = selectedEffort;
      if(data.model)Object.assign(info,data.model);
      const application=document.getElementById('mApplyStatus');if(application && current())application.textContent=modelApplicationLabel(info);
      hint('已保存，下一条消息使用新设置。');
      if (current()) {
        try { await loadState(); }
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
}
function bindModelCatalogRefresh(ai, info, busy) {
  const button=document.getElementById('mRefreshCatalog'),box=document.getElementById('modelSettings');
  if(!button || !box)return;
  const rid=RID;
  button.onclick=async()=>{
    if(busy || button.disabled)return;button.disabled=true;
    try{
      const [ok,result]=await post('/api/r/'+encodeURIComponent(rid)+'/models/refresh',{ai});
      if(RID!==rid || document.getElementById('modelSettings')!==box)return;
      if(!ok)throw Error(result.error || '刷新失败');
      const next=result.model;Object.assign(info,next);
      document.getElementById('mCatalogStatus').textContent=modelCatalogLabel(info);
      document.getElementById('mApplyStatus').textContent=modelApplicationLabel(info);
      const choice=document.getElementById('mModelChoice');
      if(choice){const selected=choice.value;choice.innerHTML='<option value="">'+esc(info.default_model?'会话默认：'+info.default_model:'使用程序默认模型')+'</option>'+(info.models || []).map(value=>'<option value="'+esc(value)+'">'+esc(value)+'</option>').join('')+'<option value="__custom__">自定义模型 ID…</option>';choice.value=[...(info.models || []),'','__custom__'].includes(selected)?selected:'__custom__';const input=document.getElementById('mModel');if(input && input.oninput)input.oninput();}
    }catch(error){if(RID===rid && document.getElementById('modelSettings')===box)document.getElementById('mCatalogStatus').textContent=error.message;}
    finally{if(RID===rid && document.getElementById('modelSettings')===box)button.disabled=busy;}
  };
}
