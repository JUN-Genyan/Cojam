const copyButton=document.getElementById('copy-git');
copyButton.addEventListener('click',async()=>{
 const code=document.getElementById('git-command').textContent;
 const status=document.getElementById('copy-status');
 try{
  if(!navigator.clipboard||!window.isSecureContext)throw new Error('clipboard unavailable');
  await navigator.clipboard.writeText(code);status.textContent='已复制命令。';
 }catch(e){status.textContent='无法自动复制，请手动选择上方命令。';}
});
