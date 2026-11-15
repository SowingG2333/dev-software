'use strict';
async function connectQtTransport() {
 if(!globalThis.qt?.webChannelTransport)return null;
 if(typeof QWebChannel!=='function')throw Error('Qt WebChannel 脚本未加载。');
 const channel=await new Promise(resolve=>new QWebChannel(qt.webChannelTransport,resolve));
 const bridge=channel.objects.applicationService, pending=new Map();let sequence=0;
 bridge.completed.connect((id,status,payload)=>{
  const entry=pending.get(id);if(!entry)return;pending.delete(id);clearTimeout(entry.timer);
  if(status>=200&&status<300)entry.resolve(payload);
  else {const error=Error(payload.error||'桌面服务请求失败');error.status=status;entry.reject(error)}
 });
 return (operationId,args)=>new Promise((resolve,reject)=>{
  const id=String(++sequence),timer=setTimeout(()=>{pending.delete(id);reject(Error('桌面请求超时，请查询任务状态。'))},130000);
  pending.set(id,{resolve,reject,timer});bridge.invoke(id,operationId,args);
 });
}
