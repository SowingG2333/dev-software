/* UI persistence port. The default is localStorage; a backend adapter can replace it. */
'use strict';
const workspaceStore = {
 key:'backnest.demo.v1',
 port:null,
 queue:Promise.resolve(),
 loadLocal(){try{return migrateStoredState(JSON.parse(localStorage.getItem(this.key)))}catch{return createInitialState()}},
 save(snapshot){
  const value=structuredClone(snapshot);
  try{localStorage.setItem(this.key,JSON.stringify(value))}catch{/* State stays in memory. */}
  if(this.port){
   const port=this.port;
   this.queue=this.queue.catch(()=>{}).then(()=>port.save(value));
   this.queue.catch(()=>{if(typeof toast==='function')toast('后端配置保存失败，当前浏览器副本仍保留。')});
  }
 },
 async attach(port){
  if(!port||typeof port.load!=='function'||typeof port.save!=='function')throw Error('Workspace port must implement load() and save(state).');
  const value=await port.load();
  if(!value||!Array.isArray(value.tasks)||!Array.isArray(value.logs)||!value.settings)throw Error('Invalid backend workspace state.');
  this.port=port;
  return migrateStoredState(value);
 },
 async detach(){await this.queue;this.port=null},
};
globalThis.workspaceStore=workspaceStore;
