// The backend checks the fixed official manifest and downloads the installer.
(function(){
  const entry=document.getElementById('updateApp');
  let timer=null, latest=null;
  async function get(path){const r=await fetch(path,{cache:'no-store'});const data=await r.json();if(!r.ok)throw Error(data.error||'更新请求失败');return data}
  function render(data){
    latest=data;
    entry.classList.toggle('has-update',data.state==='available');
    const box=document.getElementById('updateContent');if(!box)return;
    let body='<p>当前版本：'+esc(data.current||'未知')+'</p>';
    if(data.state==='available')body+='<p>发现新版本 '+esc(data.latest)+'。</p>'+(data.notes?'<p>'+esc(data.notes)+'</p>':'')+'<button id="updateDownload" class="pri">下载安装程序</button><p class="hint">下载后请关闭合拍，再手动运行安装程序。原来使用解压版时，请在安装向导中选择原来的程序文件夹，以继续使用其中的 data 数据。</p>';
    else if(data.state==='downloading')body+='<p>正在下载… '+Math.floor((data.downloaded||0)*100/(data.size||1))+'%</p>';
    else if(data.state==='downloaded')body+='<p>下载完成，文件已校验。请关闭合拍后手动运行：</p><p><code>'+esc(data.path)+'</code></p>';
    else if(data.state==='current')body+='<p>当前已是最新发布版本（'+esc(data.latest)+'）。</p>';
    else body+='<p role="alert">'+esc(data.error||'正在检查更新…')+'</p>';
    box.innerHTML=body;
    const button=document.getElementById('updateDownload');
    if(button)button.onclick=async()=>{button.disabled=true;try{const [ok,result]=await post('/api/update/download',{});if(!ok)throw Error(result.error||'下载失败');render(result);poll()}catch(e){box.insertAdjacentHTML('beforeend','<p role="alert">'+esc(e.message)+'</p>');button.disabled=false}};
    if(data.state==='downloading')poll();
  }
  function poll(){clearTimeout(timer);timer=setTimeout(async()=>{try{render(await get('/api/update/status'))}catch(e){const box=document.getElementById('updateContent');if(box)box.textContent='无法获取下载状态，请检查后台是否运行。'}},1000)}
  async function check(){try{render(await get('/api/update/check'))}catch(e){render({state:'error',current:latest&&latest.current,error:e.message})}}
  entry.onclick=()=>{showModal('<h3>合拍更新</h3><div id="updateContent" aria-live="polite"><p>正在检查更新…</p></div><div class="btns"><button id="updateRefresh">重新检查</button><button id="updateClose">关闭</button></div>');document.getElementById('updateClose').onclick=()=>{clearTimeout(timer);hideModal()};document.getElementById('updateRefresh').onclick=check;check()};
  setTimeout(()=>get('/api/update/check').then(data=>{latest=data;entry.classList.toggle('has-update',data.state==='available')}).catch(()=>{}),5000);
})();
