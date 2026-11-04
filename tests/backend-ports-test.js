'use strict';
(async()=>{
 const results=[];
 const assert=(value,message)=>{if(!value)throw Error(message)};
 async function check(name,fn){try{await fn();results.push('PASS '+name)}catch(error){results.push('FAIL '+name+': '+error.message)}}
 await check('Every declared operation can be sent through the frontend port',async()=>{
  for(const [id,operation] of Object.entries(BACKEND_OPERATIONS)){
   let called=false;
   const client=new BackNestBackend({token:'unit-test-token',fetchImpl:async(url,options)=>{
    called=true;assert(options.method===operation.method,'Wrong method for '+id);
    assert(options.headers['X-BackNest-Token']==='unit-test-token','Missing token');
    assert(!url.includes('{'),'Unresolved path parameter');
    return {ok:true,json:async()=>({id:'test-response'})};
   }});
   const path={};for(const match of operation.path.matchAll(/\{([^}]+)\}/g))path[match[1]]='id with / and ?';
   await client.invoke(id,{path,...(['POST','PATCH'].includes(operation.method)?{body:{test:true}}:{})});
   assert(called,'Operation never reached transport');
  }
 });
 await check('Structured 501 errors stay errors, not successful jobs',async()=>{
  const client=new BackNestBackend({fetchImpl:async()=>({ok:false,status:501,json:async()=>({error:'Not implemented',code:'NOT_IMPLEMENTED',capability:'compression.compress'})})});
  let failure;
  try{await client.invoke('compression.compress',{body:{}})}catch(error){failure=error}
  assert(failure?.status===501&&failure.code==='NOT_IMPLEMENTED'&&failure.operationId==='compression.compress','Error contract lost');
 });
 await check('Query values are URL encoded',async()=>{
  const path='/目录 with spaces/?&';let sent='';
  const client=new BackNestBackend({fetchImpl:async(url)=>{sent=url;return{ok:true,json:async()=>({})}}});
  await client.invoke('filesystem.directories',{query:{path}});
  assert(new URL('http://test'+sent).searchParams.get('path')===path,'Query path changed');
 });
 await check('Missing identifiers and unknown operations fail before I/O',async()=>{
  let calls=0;const client=new BackNestBackend({fetchImpl:async()=>{calls++;throw Error('unexpected I/O')}});
  for(const id of ['jobs.get','does.not.exist']){let failed=false;try{await client.invoke(id)}catch{failed=true}assert(failed,'Invalid call accepted')}
  assert(calls===0,'Invalid request reached transport');
 });
 await check('Qt transport can replace HTTP without changing operation IDs',async()=>{
  let received;
  const client=new BackNestBackend({transport:async(id,args)=>{received={id,args};return {id:'qt-job'}}});
  const result=await client.invoke('backup.start',{body:{source:'/source',destination:'/backup'}});
  assert(received.id==='backup.start'&&result.id==='qt-job','Qt port not used');
 });
 await check('Workspace persistence is replaceable and serializes independent snapshots',async()=>{
  const originalKey=workspaceStore.key;
  workspaceStore.key='backnest.isolated-port-test';
  try{
   const saved=[];
   const loaded=await workspaceStore.attach({load:async()=>createInitialState(),save:async value=>saved.push(value)});
   loaded.tasks.push({id:'owned-test'});workspaceStore.save(loaded);loaded.tasks[0].id='changed-after-save';
   await workspaceStore.queue;
   assert(saved[0].tasks[0].id==='owned-test','Save did not isolate its snapshot');
   await workspaceStore.detach();
  }finally{localStorage.removeItem(workspaceStore.key);workspaceStore.key=originalKey}
 });
 await check('Invalid backend workspace does not replace the persistence adapter',async()=>{
  let failed=false;try{await workspaceStore.attach({load:async()=>({bad:true}),save:async()=>{}})}catch{failed=true}
  assert(failed&&workspaceStore.port===null,'Invalid state was attached');
 });
 document.getElementById('results').textContent=results.join('\n');
 document.body.dataset.passed=String(results.every(line=>line.startsWith('PASS')));
})();
