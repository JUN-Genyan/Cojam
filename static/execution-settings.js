function executionSettingsMarkup(ai, owner, busy) {
  if (!owner || !['cli','desktop'].includes(owner.endpoint)) return '';
  return '<section id="executionSettings"><h3>执行端</h3><p class="hint" id="executionCurrent">'+esc(owner.label)+'</p><p class="hint">CLI 直连；桌面通过 MCP 待命。每条原生会话同时只由一个端接管，不随在线状态自动切换。</p><p class="hint">原生会话：<code>'+esc(owner.native_session || '尚未建立')+'</code></p><div id="executionControls"></div><p id="executionMessage" class="hint" role="status" aria-live="polite"></p><div id="executionInstruction"></div><button type="button" id="executionAgentSettings">打开 Agent 配置</button></section>';
}
function bindExecutionSettings(ai, owner, busy) {
  const box=document.getElementById('executionSettings');if(!box)return;
  const room=RID,current=()=>RID===room && document.getElementById('executionSettings')===box;
  let state=owner,working=false,timer=null,controller=new AbortController();
  const message=text=>{if(current())box.querySelector('#executionMessage').textContent=text;};
  const render=()=>{
    if(!current())return;
    box.querySelector('#executionCurrent').textContent=state.label;
    const controls=box.querySelector('#executionControls');
    controls.innerHTML=state.phase!=='ready'?'<button id="executionCancel">取消接管，保留原执行端</button>'
      :state.handoff_supported?'<button id="executionSwitch"'+(busy?' disabled':'')+'>'+(state.endpoint==='cli'?'由桌面接管同一会话':'释放给 CLI')+'</button>'
      :'<p class="hint">此 Agent 暂无已验证的原生会话共享适配，保留当前执行端。</p>';
    const button=controls.querySelector('button');if(button)button.onclick=()=>change(state.phase==='ready'?'switch':'cancel');
  };
  const change=async op=>{
    if(working || !current())return;working=true;message('正在处理执行端交接…');
    try{
      const [ok,result]=await post('/api/r/'+encodeURIComponent(room)+'/execution',{ai,version:state.version,op,endpoint:state.endpoint==='cli'?'desktop':'cli'});
      if(!current())return;if(!ok)throw Error(result.error || '无法切换执行端');
      state=result.execution;render();
      const instruction=box.querySelector('#executionInstruction');instruction.innerHTML='';
      if(result.instruction){
        instruction.innerHTML='<p class="hint">先在桌面打开指定原生会话，再把这条指令发给桌面 Agent。原会话中的工具记录验证通过后自动完成接管。</p><pre class="execution-prompt"></pre><button id="executionCopy">复制桌面接管指令</button>'+(result.native_command?'<button id="executionNativeCommand">复制打开原生会话的命令</button>':'');
        instruction.querySelector('pre').textContent=result.instruction;
        instruction.querySelector('#executionCopy').onclick=()=>copyText(result.instruction);
        const native=instruction.querySelector('#executionNativeCommand');if(native)native.onclick=()=>copyText(result.native_command);
        message('等待桌面从同一条原生会话确认接管，五分钟内有效。');
      }else message(state.phase==='ready'?'执行端已更新。':'等待桌面释放确认；离线超时后可取消并重新切换。');
      await loadState();if(current() && state.phase==='ready')openAi(ai);
    }catch(error){message(error.message);}finally{working=false;}
  };
  box.querySelector('#executionAgentSettings').onclick=()=>openAgentSettings(ai);
  render();
  const poll=async()=>{
    if(!current()){controller.abort();clearTimeout(timer);return;}
    try{
      const response=await fetch('/api/r/'+encodeURIComponent(room)+'/state',{signal:controller.signal});
      const value=await response.json();if(!current())return;
      const next=(value.execution || {})[ai];
      if(next){const changed=next.version!==state.version;state=next;render();if(changed && state.phase==='ready'){box.querySelector('#executionInstruction').innerHTML='';message('接管已完成：'+state.label+'。');await loadState();if(current())openAi(ai);}}
    }catch(error){if(current() && !controller.signal.aborted)message('暂时无法刷新接管状态，请检查连接。');}
    if(current())timer=setTimeout(poll,2000);
  };
  timer=setTimeout(poll,2000);
}
