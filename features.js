'use strict';
function extendedOptions(w) {
 const values=w.filterForm||{};
 const input=(key,label,type='text',placeholder='留空不限')=>`<div class="field"><label for="filter-${key}">${label}</label><input id="filter-${key}" type="${type}" value="${esc(values[key]||'')}" placeholder="${placeholder}" ${type==='number'?'min="0" step="1"':''}></div>`;
 return `<div class="two-fields"><div class="field"><label for="task-type">备份方式</label><select id="task-type"><option>完整备份</option></select></div><div class="field"><label for="task-schedule">执行方式</label><select id="task-schedule"><option>手动备份</option></select></div></div>
 <div class="two-fields">${input('include','包含相对路径','text','例如 docs/*；多条用分号分隔')}${input('exclude','排除相对路径','text','例如 cache；多条用分号分隔')}${input('names','文件名','text','例如 *.txt;*.md')}
 <div class="field"><label for="filter-types">文件类型</label><select id="filter-types">${[['','全部'],['regular','普通文件'],['directory','目录']].map(([v,t])=>`<option value="${v}" ${values.types===v?'selected':''}>${t}</option>`).join('')}</select></div>
 ${input('from','修改时间起','datetime-local')}${input('to','修改时间止','datetime-local')}${input('min','最小字节数','number')}${input('max','最大字节数','number')}${input('owners','属主 SID / UID','text','本机 SID 或 UID；分号分隔')}</div>
 <p class="subtext">同维度多条规则取并集，不同维度取交集。* 匹配任意字符（含 /），? 匹配一个字符；大小写敏感。排除目录会排除其子项；保留选中文件的父目录。</p>
 <div class="feature-checks">${[['archive','保存为单文件容器'],['compression','启用自实现 RLE 压缩（自动使用容器）'],['times','保留修改时间'],['permissions','保留基本权限（Windows 仅只读属性）']].map(([key,label])=>`<label><input type="checkbox" id="option-${key}" ${w[key]?'checked':''}>${label}</label>`).join('')}</div>
 <p class="subtext">符号链接、重解析点、特殊文件及 ACL/xattrs 恢复暂不支持，遇到会报错；硬链接按普通文件内容复制。解包默认最多 8 GiB。</p>
 <button class="btn" data-native="preview">预览实际备份清单</button><div id="filter-preview" class="subtext" role="status"></div>`;
}
function readExtendedOptions(w) {
 w.filterForm={};
 for(const key of ['include','exclude','names','types','from','to','min','max','owners']) w.filterForm[key]=document.getElementById('filter-'+key).value.trim();
 for(const key of ['archive','compression','times','permissions']) w[key]=document.getElementById('option-'+key).checked;
 const f=w.filterForm, list=key=>f[key].split(';').map(s=>s.trim()).filter(Boolean), filters={};
 if(f.include||f.exclude)filters.paths={include:list('include'),exclude:list('exclude')};
 if(f.names)filters.names=list('names');
 if(f.types)filters.types=[f.types];
 if(f.from||f.to){filters.time={field:'modified'};for(const key of ['from','to'])if(f[key])filters.time[key]=new Date(f[key]).toISOString()}
 if(f.min||f.max){filters.size={};for(const [key,name] of [['min','minBytes'],['max','maxBytes']])if(f[key]){const n=Number(f[key]);if(!Number.isSafeInteger(n)||n<0)throw Error('尺寸必须是非负整数。');filters.size[name]=n}}
 if(f.owners)filters.users=list('owners').map(id=>({platform:nativeSession?.platform==='win32'?'windows':'posix',id}));
 w.options={mode:'full',filters};
 if(w.archive||w.compression)w.options.archive={algorithm:'bkn-container-v2'};
 if(w.compression)w.options.compression={algorithm:'rle-block-v1'};
 const preserve=['times','permissions'].filter(key=>w[key]);
 if(preserve.length)w.options.metadata={preserve,unsupported:'fail'};
}
async function previewWizard() {
 try {
  readWizard();
  const panel=document.getElementById('filter-preview');panel.textContent='正在扫描…';
  const result=await nativeRequest('filters.preview',{body:{source:wizard.path,filters:wizard.options.filters}});
  if(panel.isConnected)panel.innerHTML=`<p>包含 ${result.files} 个文件，${formatSize(result.bytes)}；排除 ${result.excluded.length} 项。</p><pre class="preview-list">${esc(result.included.slice(0,200).map(e=>`${e.kind==='directory'?'目录':'文件'}  ${e.path}`).join('\n'))}</pre>${result.included.length>200?'仅显示前 200 项；备份将使用完整清单。':''}<p>预览不会锁定源目录；运行时将重新扫描，写入时发生变化则报错。</p>`;
 } catch(error){toast(error.message)}
}
async function cancelActiveJob() {
 const id=activeNativeJob||state.tasks.find(t=>t.activeJob)?.activeJob;
 if(!id)return;
 try{await nativeRequest('jobs.cancel',{path:{jobId:id},body:{}});toast('已请求取消，正在清理临时文件。')}catch(error){toast(error.message)}
}
