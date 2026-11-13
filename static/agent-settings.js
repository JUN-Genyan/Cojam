// Persistent, structured Agent preferences. No raw configuration editor.
let agentSettingsSequence = 0;
const AGENT_ARG_LABELS = {base:'通用启动参数',new:'新会话参数',resume:'续接会话参数',read:'讨论模式参数',work:'干活模式参数',model:'模型参数',effort:'思考强度参数',acp:'ACP 启动参数'};
const AGENT_EFFORTS = ['none','minimal','low','medium','high','xhigh','max'];
function agentInput(id, label, value, extra = '') {
  return '<label for="'+id+'">'+label+'</label><input id="'+id+'" value="'+esc(value || '')+'" '+extra+'>';
}
function agentPermissionFields(values, attribute) {
  const defaults = {read:true,post:true,trigger:true,board:false,files:false,work:false};
  return '<div class="perm-grid">'+Object.entries(PERM_LABELS).map(([key,label])=>'<label><input type="checkbox" '+attribute+'="'+key+'"'+((values[key] === undefined ? defaults[key] : values[key])?' checked':'')+'>'+esc(label)+'</label>').join('')+'</div>';
}
function agentListRow(value, placeholder) {
  return '<div class="agent-value-row"><input data-agent-value value="'+esc(value)+'" placeholder="'+esc(placeholder || '一个参数，可包含空格')+'"><button type="button" data-agent-remove aria-label="删除这一项">×</button></div>';
}
function agentListEditor(key, values, placeholder) {
  return '<div class="agent-list-editor" data-agent-list="'+key+'"><div data-agent-rows>'+values.map(v=>agentListRow(v,placeholder)).join('')+'</div><button type="button" data-agent-add>＋ 添加一项</button></div>';
}
function agentModelRow(id, efforts) {
  const levels = [...new Set([...AGENT_EFFORTS,...efforts])];
  return '<div class="agent-model-row"><div class="agent-value-row"><input data-agent-model-id value="'+esc(id)+'" placeholder="模型 ID"><button type="button" data-agent-remove-model aria-label="删除模型">×</button></div><div class="agent-efforts">'+levels.map(e=>'<label><input type="checkbox" data-agent-model-effort="'+esc(e)+'"'+(efforts.includes(e)?' checked':'')+'>'+esc(e)+'</label>').join('')+'</div></div>';
}
function agentSettingsMarkup(config) {
  const v = config.values, caps = config.capabilities || {}, desktop = v.desktop || {};
  const local = ['cli','acp','claude','codex'].includes(config.driver), builtin = ['claude','codex'].includes(config.driver);
  const models = [...new Set([...(caps.models || []),...(v.models || []),...(v.default[0]?[v.default[0]]:[])])];
  const modelMap = v.model_efforts || {};
  let html = '<div id="agentSettings" class="agent-settings"><div class="connection-heading"><span class="eyebrow">Agent 设置</span><button type="button" id="agentSettingsClose" aria-label="关闭设置">×</button></div>'
    +'<h3>'+esc(v.label)+'</h3><p class="hint">@'+esc(config.id)+' · '+esc(AG_DRV[config.driver] || config.driver)+' · '+(config.enabled?'已检测到':'程序未找到或未启用')+'</p>'
    +'<nav class="agent-settings-tabs" aria-label="Agent 设置分组">'+[['general','基本'],['cli','CLI'],['desktop','桌面 / MCP'],['models','模型'],['joining','加入会话']].map(([id,label])=>'<button type="button" data-agent-tab="'+id+'" aria-pressed="'+(id==='general')+'">'+label+'</button>').join('')+'</nav>'
    +'<section data-agent-panel="general">'+agentInput('agentLabel','名称',v.label,'maxlength="40"')+'<p class="hint">联系人 ID 保持不变，已有会话和 @ 引用继续使用。默认模型和加入规则只作为默认值；各会议的单独设置优先。</p>'
    +(config.driver==='http'?agentInput('agentEndpoint','服务地址',v.endpoint,'type="url"'):'')
    +'<button type="button" id="agentConnectionGuide">连接验证与接入</button></section>';
  html += '<section data-agent-panel="cli" hidden>'+(local?'<h4>程序位置与备用命令</h4><p class="hint">按顺序查找程序。完整路径无需加引号；修改后下一次调用使用新位置。</p>'+agentListEditor('bin',v.bin || [],'程序完整路径或命令名称')+'<button type="button" id="agentBrowseCli" data-agent-file="cli" hidden>选择 CLI 程序…</button>'
    +'<details><summary>启动参数</summary><p class="hint">每项单独输入，不需要引号或 JSON。可使用 {prompt}、{session}、{model}、{effort}、{cwd} 占位符。</p>'
    +Object.entries(AGENT_ARG_LABELS).filter(([key])=>!builtin || key==='base').map(([key,label])=>'<h4>'+label+'</h4>'+agentListEditor('arg-'+key,(v.args || {})[key] || [])).join('')+'</details>'
    +(!builtin?'<label for="agentPrompt">提示词传递</label><select id="agentPrompt"><option value="arg"'+(v.prompt==='arg'?' selected':'')+'>命令行参数</option><option value="stdin"'+(v.prompt==='stdin'?' selected':'')+'>标准输入</option></select><label for="agentContext">聊天记录传递</label><select id="agentContext"><option value="delta"'+(v.context==='delta'?' selected':'')+'>只传新增内容</option><option value="full"'+(v.context==='full'?' selected':'')+'>传递会话记录</option></select>':'')
    :'<p class="hint">此联系人当前通过 '+esc(AG_DRV[config.driver] || config.driver)+' 接入，没有本机 CLI 启动项。</p>')+'</section>';
  html += '<section data-agent-panel="desktop" hidden><p class="hint">设置桌面程序的位置和 MCP 保存位置，应用会自动写入接入配置。写入后重启桌面软件，并开启 /cojam_standby；收到待命心跳后才显示在线。</p>'
    +agentInput('agentDesktopPath','桌面程序位置',desktop.path,'placeholder="程序完整路径"')+'<button type="button" data-agent-file="desktop" hidden>选择桌面程序…</button>'
    +'<label for="agentDesktopHost">桌面软件 / MCP 格式</label><select id="agentDesktopHost"><option value="">请选择软件</option>'+(config.hosts || []).map(h=>'<option value="'+esc(h.id)+'"'+(desktop.host===h.id?' selected':'')+'>'+esc(h.label)+'</option>').join('')+'</select>'
    +agentInput('agentMcpPath','MCP 保存位置',desktop.mcp_path,'placeholder="可使用自动填入的位置，也可手动选择"')+'<button type="button" data-agent-file="mcp" hidden>选择已有 MCP 文件…</button>'
    +'<p class="hint" id="agentMcpPathHint"></p>'
    +(config.connection.has_key?'<label class="agent-check"><input type="checkbox" id="agentReplaceKey">应用时更换已有接入密钥（旧客户端需重新接入）</label>':'')
    +'<button type="button" id="agentApplyMcp">保存并应用 MCP 配置</button><button type="button" id="agentStopStandby">停止待命</button></section>';
  html += '<section data-agent-panel="models" hidden><p class="hint">这里设置此 Agent 的默认值；会议室里仍可为每场会议单独选择模型和 effort。</p>'
    +agentInput('agentDefaultModel','默认模型',v.default[0],'list="agentModelOptions" placeholder="选择或输入模型 ID"')+'<datalist id="agentModelOptions">'+models.map(m=>'<option value="'+esc(m)+'"></option>').join('')+'</datalist>'
    +'<label for="agentDefaultEffort">默认 effort</label><select id="agentDefaultEffort"></select><p class="hint" id="agentEffortHint"></p>'
    +'<details><summary>编辑可选模型和 effort</summary><p class="hint">可补充当前接入支持的模型。勾选该模型可用的 effort；此索引不改变账户权限，也不会让不支持切换的客户端获得切换能力。</p><div id="agentModelRows">'
    +(v.models || []).map(m=>agentModelRow(m,modelMap[m] || v.efforts || [])).join('')+'</div><button type="button" id="agentAddModel">＋ 添加模型</button></details>'
    +'<p class="hint">'+esc(caps.note || '')+'</p></section>';
  html += '<section data-agent-panel="joining" hidden><label for="agentDefaultJoin">新建会话时默认邀请</label><select id="agentDefaultJoin"><option value="auto"'+(v.default_join==null?' selected':'')+'>沿用当前推荐规则</option><option value="yes"'+(v.default_join===true?' selected':'')+'>默认勾选此 Agent</option><option value="no"'+(v.default_join===false?' selected':'')+'>默认不勾选</option></select><h4>新加入会话的默认权限</h4>'+agentPermissionFields(v.default_permissions || {},'data-agent-default-permission')
    +'<p class="hint">修改默认值不会覆盖已经授予的会话权限。干活还需要会议处于干活模式。</p><h4>已有会话</h4><div id="agentJoinedRooms">正在读取…</div></section>'
    +'<p id="agentSettingsMessage" class="hint" role="status" aria-live="polite"></p><div class="btns agent-settings-actions"><button type="button" id="agentSettingsCancel">关闭</button><button type="button" class="pri" id="agentSettingsSave">保存设置</button></div></div>';
  return html;
}
function collectAgentSettings(root, config) {
  const v = config.values, changes = {}, get = id=>root.querySelector('#'+id);
  const stable=value=>JSON.stringify(value,(_,item)=>item && !Array.isArray(item) && typeof item==='object'?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
  const put = (key,value)=>{if(stable(value)!==stable(v[key]))changes[key]=value;};
  put('label',get('agentLabel').value.trim());
  if(get('agentEndpoint'))put('endpoint',get('agentEndpoint').value.trim());
  const lists = {};
  root.querySelectorAll('[data-agent-list]').forEach(box=>{lists[box.dataset.agentList]=[...box.querySelectorAll('[data-agent-value]')].map(input=>input.value).filter(value=>value!=='');});
  if(lists.bin)put('bin',lists.bin.map(value=>value.trim()));
  const args = {...v.args};
  Object.entries(lists).filter(([key])=>key.startsWith('arg-')).forEach(([key,value])=>{args[key.slice(4)]=value;});
  if(lists.bin)put('args',args);
  if(get('agentPrompt'))put('prompt',get('agentPrompt').value);
  if(get('agentContext'))put('context',get('agentContext').value);
  const desktop={path:get('agentDesktopPath').value.trim(),host:get('agentDesktopHost').value,mcp_path:get('agentMcpPath').value.trim()};
  put('desktop',desktop);
  put('default',[get('agentDefaultModel').value.trim(),get('agentDefaultEffort').value]);
  const join=get('agentDefaultJoin').value;
  put('default_join',join==='auto'?null:join==='yes');
  const permissions=Object.fromEntries([...root.querySelectorAll('[data-agent-default-permission]')].map(i=>[i.dataset.agentDefaultPermission,i.checked]));
  const defaults={read:true,post:true,trigger:true,board:false,files:false,work:false,...v.default_permissions};
  if(JSON.stringify(permissions)!==JSON.stringify(defaults))put('default_permissions',permissions);
  const models=[], perModel={...v.model_efforts};
  root.querySelectorAll('.agent-model-row').forEach(row=>{const id=row.querySelector('[data-agent-model-id]').value.trim();if(!id)return;models.push(id);perModel[id]=[...row.querySelectorAll('[data-agent-model-effort]:checked')].map(i=>i.dataset.agentModelEffort);});
  put('models',[...new Set(models)]);
  // Don't create per-model overrides merely by opening an existing form.
  const originalRows=(v.models || []).map(id=>[id,(v.model_efforts || {})[id] || v.efforts || []]);
  const editedRows=models.map(id=>[id,perModel[id]]);
  if(JSON.stringify(originalRows)!==JSON.stringify(editedRows)){
    (v.models || []).filter(id=>!models.includes(id)).forEach(id=>delete perModel[id]);
    put('model_efforts',perModel);
  }
  return changes;
}
async function openAgentSettings(id, tab = 'general') {
  const seq=++agentSettingsSequence;
  document.body.classList.remove('nav-open');
  stopConnectionWizard();
  showModal('<div id="agentSettingsLoading"><h3>正在读取 Agent 设置…</h3><div class="btns"><button onclick="hideModal()">关闭</button></div></div>');
  const loading=document.getElementById('agentSettingsLoading');
  try{
    const [ok,config]=await connectionGet('/api/agents/config?id='+encodeURIComponent(id));
    if(seq!==agentSettingsSequence || document.getElementById('agentSettingsLoading')!==loading)return;
    if(!ok)throw Error(config.error || '读取 Agent 设置失败');
    showModal(agentSettingsMarkup(config));bindAgentSettings(config);
    const choice = document.querySelector('[data-agent-tab="'+tab+'"]');if(choice)choice.click();
  }catch(error){if(seq===agentSettingsSequence && document.getElementById('agentSettingsLoading')===loading)showModal('<h3>无法打开设置</h3><p role="alert">'+esc(error.message)+'</p><button onclick="hideModal()">关闭</button>');}
}
function bindAgentSettings(config) {
  const root=document.getElementById('agentSettings'),get=id=>root.querySelector('#'+id);
  const current=()=>document.getElementById('agentSettings')===root;
  let saving=false;
  const message=text=>{if(current())get('agentSettingsMessage').textContent=text;};
  root.querySelectorAll('[data-agent-tab]').forEach(button=>button.onclick=()=>{
    root.querySelectorAll('[data-agent-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    root.querySelectorAll('[data-agent-panel]').forEach(panel=>panel.hidden=panel.dataset.agentPanel!==button.dataset.agentTab);
  });
  const close=()=>{if(saving)return;if(Object.keys(collectAgentSettings(root,config)).length && !confirm('尚有未保存的 Agent 设置，关闭并放弃这些修改？'))return;hideModal();};
  get('agentSettingsClose').onclick=close;get('agentSettingsCancel').onclick=close;
  root.onclick=event=>{
    const remove=event.target.closest('[data-agent-remove]');if(remove)remove.closest('.agent-value-row').remove();
    const add=event.target.closest('[data-agent-add]');if(add){const rows=add.closest('[data-agent-list]').querySelector('[data-agent-rows]');rows.insertAdjacentHTML('beforeend',agentListRow(''));rows.lastElementChild.querySelector('input').focus();}
    const model=event.target.closest('[data-agent-remove-model]');if(model){model.closest('.agent-model-row').remove();refreshEfforts();}
  };
  get('agentAddModel').onclick=()=>{get('agentModelRows').insertAdjacentHTML('beforeend',agentModelRow('',[]));get('agentModelRows').lastElementChild.querySelector('input').focus();};
  const refreshEfforts=()=>{
    const model=get('agentDefaultModel').value.trim(),values=config.values,caps=config.capabilities || {};
    const row=[...root.querySelectorAll('.agent-model-row')].find(r=>r.querySelector('[data-agent-model-id]').value.trim()===model);
    const levels=row?[...row.querySelectorAll('[data-agent-model-effort]:checked')].map(i=>i.dataset.agentModelEffort)
      :Object.prototype.hasOwnProperty.call(values.model_efforts || {},model)?values.model_efforts[model]
      :Object.prototype.hasOwnProperty.call(caps.efforts_by_model || {},model)?caps.efforts_by_model[model]:values.efforts || [];
    const select=get('agentDefaultEffort'),previous=select.dataset.initialized?select.value:values.default[1];
    const options=[...new Set(levels)];if(!select.dataset.initialized && previous && !options.includes(previous))options.push(previous);
    select.innerHTML='<option value="">使用程序默认值</option>'+options.map(v=>'<option value="'+esc(v)+'">'+esc(v)+'</option>').join('');
    select.value=options.includes(previous)?previous:'';select.dataset.initialized='true';
    get('agentEffortHint').textContent=options.length?'可选 effort 随模型变化。':'当前索引没有此模型的 effort 选项，可在下方补充。';
  };
  get('agentDefaultModel').oninput=refreshEfforts;get('agentModelRows').onchange=refreshEfforts;refreshEfforts();
  const hostChanged=()=>{const host=config.hosts.find(h=>h.id===get('agentDesktopHost').value);get('agentMcpPath').disabled=!!host && host.kind==='claude';get('agentMcpPathHint').textContent=host && host.kind==='claude'?'此宿主通过 Claude 命令管理 MCP，无需指定保存位置。':'应用会保留其他 MCP 项目，并备份原文件。';};
  get('agentDesktopHost').onchange=()=>{const host=config.hosts.find(h=>h.id===get('agentDesktopHost').value);get('agentMcpPath').value=host?host.path:'';hostChanged();};hostChanged();
  const fileButtons=[...root.querySelectorAll('[data-agent-file]')];
  const exposePicker=()=>{if(!current())return;const api=window.pywebview && window.pywebview.api;fileButtons.forEach(b=>b.hidden=!(api && api.choose_agent_file));};
  exposePicker();window.addEventListener('pywebviewready',exposePicker,{once:true});
  fileButtons.forEach(button=>button.onclick=async()=>{button.disabled=true;try{const path=await window.pywebview.api.choose_agent_file();if(!current() || !path)return;const kind=button.dataset.agentFile;if(kind==='cli'){const box=root.querySelector('[data-agent-list="bin"] [data-agent-rows]');if(!box.firstElementChild)box.innerHTML=agentListRow('');box.querySelector('input').value=path;}else get(kind==='desktop'?'agentDesktopPath':'agentMcpPath').value=path;}catch(error){message(error.message || '无法选择程序');}finally{button.disabled=false;}});
  const save=async()=>{
    if(saving)return false;
    let changes;try{changes=collectAgentSettings(root,config);}catch(error){message(error.message);return false;}
    if(!Object.keys(changes).length){message('设置未发生变化。');return true;}
    saving=true;get('agentSettingsSave').disabled=true;message('正在保存…');
    try{
      const [ok,result]=await post('/api/agents/config',{id:config.id,revision:config.revision,changes});
      if(!ok)throw Error(result.error || '保存失败');
      config=result.config;
      if(current()){
        // Keep the user's active tab and inputs; use the server's revision for subsequent saves.
        message('已保存。默认值与程序位置已更新；已有会议的单独设置保留。');
        root.querySelector('h3').textContent=config.values.label;
      }
      await refreshContacts();if(current() && RID)await loadState();
      return true;
    }catch(error){message(error.message || '保存失败，请重试');return false;}
    finally{saving=false;if(current())get('agentSettingsSave').disabled=false;}
  };
  get('agentSettingsSave').onclick=save;
  get('agentApplyMcp').onclick=async()=>{
    const replace=get('agentReplaceKey');if(config.connection.has_key && !(replace && replace.checked)){message('请确认更换已有密钥；这会使旧客户端的密钥失效。');return;}
    if(!await save() || !current())return;
    const button=get('agentApplyMcp');button.disabled=true;message('正在应用 MCP 配置…');
    try{const [ok,result]=await post('/api/agents/config/mcp',{id:config.id,revision:config.revision,replace_key:!!(replace && replace.checked)});if(!ok)throw Error(result.error || '应用失败');config.connection.has_key=true;message(result.message);if(!get('agentReplaceKey'))button.insertAdjacentHTML('beforebegin','<label class="agent-check"><input type="checkbox" id="agentReplaceKey">应用时更换已有接入密钥（旧客户端需重新接入）</label>');await refreshContacts();}catch(error){message(error.message);}finally{if(current())button.disabled=false;}
  };
  get('agentStopStandby').onclick=async()=>{const [ok,result]=await post('/api/members/standby/stop',{id:config.id});message(ok?'待命已停止。':result.error || '停止失败');await refreshContacts();};
  get('agentConnectionGuide').onclick=async()=>{if(Object.keys(collectAgentSettings(root,config)).length && !await save())return;if(current())startConnectionWizard(config.id);};
  const membership=async()=>{
    await pollRooms();if(!current())return;
    get('agentJoinedRooms').innerHTML=ROOMS.filter(r=>!r.archived).map(r=>'<div class="agent-room-row"><span>'+esc(r.title)+'<small>'+((r.participants || []).includes(config.id)?'已加入':'未加入')+'</small></span><button type="button" data-agent-room="'+esc(r.id)+'">设置成员与权限</button></div>').join('') || '<p class="hint">暂无会话，创建后可在这里设置。</p>';
    root.querySelectorAll('[data-agent-room]').forEach(button=>button.onclick=async()=>{if(Object.keys(collectAgentSettings(root,config)).length && !await save())return;if(current())openAgentMembership(config.id,button.dataset.agentRoom);});
  };membership().catch(error=>message(error.message));
}
async function openAgentManager() {
  document.body.classList.remove('nav-open');
  await refreshContacts(null,true);
  showModal('<div id="agentManager"><h3>Agent 设置</h3><p class="hint">已接入和暂未找到程序的 Agent 都可以修改。</p>'+CONTACTS.filter(a=>a.kind!=='user').map(a=>'<div class="agent-room-row"><span>'+esc(a.label)+'<small>@'+esc(a.id)+' · '+(a.enabled?'已检测到':'未找到程序')+'</small></span><button type="button" data-agent-settings="'+esc(a.id)+'">配置</button></div>').join('')+'<div class="btns"><button onclick="hideModal()">关闭</button><button id="agentManagerAdd" class="pri">添加 Agent</button></div></div>');
  mbox.querySelectorAll('[data-agent-settings]').forEach(b=>b.onclick=()=>openAgentSettings(b.dataset.agentSettings));
  document.getElementById('agentManagerAdd').onclick=()=>startConnectionWizard('','local');
}
async function openAgentMembership(id, roomId) {
  document.body.classList.remove('nav-open');
  showModal('<div id="agentMembershipLoading"><h3>正在读取会话权限…</h3><button onclick="hideModal()">关闭</button></div>');
  const loading=document.getElementById('agentMembershipLoading');
  const [ok,state]=await connectionGet('/api/r/'+encodeURIComponent(roomId)+'/state');
  if(document.getElementById('agentMembershipLoading')!==loading)return;
  if(!ok){showModal('<h3>读取失败</h3><p>'+esc(state.error || '会话不可用')+'</p><button onclick="hideModal()">关闭</button>');return;}
  const contact=CONTACTS.find(a=>a.id===id),perms=(state.permissions || {})[id] || (contact && contact.default_permissions) || {};
  showModal('<div id="agentMembership"><h3>'+esc(contact?contact.label:id)+' · '+esc(state.title)+'</h3><label class="agent-check"><input id="agentRoomJoined" type="checkbox"'+(state.participants.includes(id)?' checked':'')+'>加入此会话</label><h4>此会话内的权限</h4>'+agentPermissionFields(perms,'data-agent-room-permission')+'<p class="hint">只修改此联系人；其他成员和权限保持不变。</p><p id="agentMembershipMessage" role="status"></p><div class="btns"><button id="agentMembershipBack">返回 Agent 设置</button><button id="agentMembershipSave" class="pri">保存</button></div></div>');
  const root=document.getElementById('agentMembership');
  document.getElementById('agentMembershipBack').onclick=()=>openAgentSettings(id);
  document.getElementById('agentMembershipSave').onclick=async event=>{const button=event.currentTarget;button.disabled=true;try{const permissions=Object.fromEntries([...root.querySelectorAll('[data-agent-room-permission]')].map(i=>[i.dataset.agentRoomPermission,i.checked]));const [ok,result]=await post('/api/r/'+encodeURIComponent(roomId)+'/invite',{contact:id,joined:root.querySelector('#agentRoomJoined').checked,permissions});if(document.getElementById('agentMembership')!==root)return;root.querySelector('#agentMembershipMessage').textContent=ok?'成员和权限已保存。':result.error || '保存失败';if(ok){await pollRooms();if(RID===roomId)await loadState();}}catch(error){if(document.getElementById('agentMembership')===root)root.querySelector('#agentMembershipMessage').textContent=error.message;}finally{button.disabled=false;}};
}
