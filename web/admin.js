(()=>{
const NAV=[
  {label:'',items:[['dashboard','/admin','总览','DB']]},
  {label:'数据引擎',items:[['domain-cn','/admin/domains/cn','中国 CN','CN'],['domain-us-app','/admin/domains/us-application','美国 Application','US'],['domain-us-assignment','/admin/domains/us-assignment','美国 Assignment','AS'],['domain-us-ttab','/admin/domains/us-ttab','美国 TTAB','TB'],['raw','/admin/raw','Raw 数据','RW'],['packages','/admin/packages','来源包','PK'],['jobs','/admin/jobs','任务中心','JB']]},
  {label:'联系人',items:[['contacts-overview','/admin/contacts','联系人总览','CO'],['contacts-directory','/admin/contacts/directory','联系人库','CD'],['contacts-imports','/admin/contacts/imports','导入任务','IM']]},
  {label:'查询',items:[['search','/admin/search','商标 / 案件查询','Q']]},
  {label:'系统',items:[['system','/admin/system','系统状态','SY']]},
];
function navHtml(active){return NAV.map(g=>`${g.label?`<div class="nav-group">${g.label}</div>`:''}${g.items.map(([key,url,label,icon])=>`<a class="nav-item ${active===key?'active':''}" href="${url}"><span class="nav-icon">${icon}</span><span>${label}</span></a>`).join('')}`).join('')}
function shell({active,title,subtitle='',crumb='MarkOrbit Data Engine'}){
  const page=document.getElementById('page-content');if(!page)return;
  const holder=document.createElement('div');holder.className='admin-shell';
  holder.innerHTML=`<aside class="sidebar" id="admin-sidebar"><div class="brand"><div class="brand-orbit"></div><div class="brand-copy"><b>MarkOrbit</b><span>Data Engine Admin</span></div></div><div class="nav-scroll">${navHtml(active)}</div><div class="sidebar-foot"><div class="mini-brand"><span class="mini-dot"></span><span>Keep Every Brand Moving in Orbit.</span></div></div></aside><div class="main"><header class="topbar"><div style="display:flex;align-items:center;gap:11px"><button class="mobile-nav" id="mobile-nav">☰</button><div><div class="crumb">${esc(crumb)}</div><div class="page-title">${esc(title)}</div>${subtitle?`<div class="page-subtitle">${esc(subtitle)}</div>`:''}</div></div><div class="top-actions"><span class="health-pill"><i class="health-dot" id="h-api"></i>API</span><span class="health-pill"><i class="health-dot" id="h-pg"></i>PG</span><span class="health-pill"><i class="health-dot" id="h-ch"></i>CH</span><span class="health-pill" id="engine-version">—</span><span class="health-pill" id="last-refresh">—</span></div></header><main class="content" id="admin-content"></main></div>`;
  document.body.insertBefore(holder,page);holder.querySelector('#admin-content').appendChild(page);
  document.getElementById('mobile-nav')?.addEventListener('click',()=>document.getElementById('admin-sidebar')?.classList.toggle('open'));
  loadHealth();setInterval(loadHealth,30000);
}
async function api(url,options){const r=await fetch(url,options);const text=await r.text();let body;try{body=JSON.parse(text)}catch{body=text}if(!r.ok)throw new Error(typeof body==='string'?body:JSON.stringify(body));return body}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function num(v){return Number(v||0).toLocaleString()}
function bytes(v){v=Number(v||0);if(!v)return'0 B';const u=['B','KB','MB','GB','TB'];let i=0;while(v>=1024&&i<u.length-1){v/=1024;i++}return`${v.toFixed(i?1:0)} ${u[i]}`}
function time(v){if(!v)return'—';try{return new Date(v).toLocaleString()}catch{return String(v)}}
function duration(v){const s=Math.max(0,Number(v||0));if(s<60)return`${s.toFixed(1)}s`;if(s<3600)return`${Math.floor(s/60)}m ${Math.floor(s%60)}s`;return`${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`}
function status(v){const x=String(v||'UNKNOWN').toUpperCase();const c={SUCCESS:'green',READY:'amber',PROCESSING:'amber',RUNNING:'amber',FAILED:'red',INVALID:'red',MISSING_FILE:'red',INTERRUPTED:'amber',REGISTERED:'blue'}[x]||'';return`<span class="badge ${c}">${esc(x)}</span>`}
function segment(v){const x=String(v||'UNKNOWN').toUpperCase();const map={AGENT:['代理','blue'],DIRECT:['直客','cyan'],BOTH:['混合','green'],UNKNOWN:['未分类','']};const [label,c]=map[x]||map.UNKNOWN;return`<span class="badge ${c}">${label}</span>`}
function query(){return new URLSearchParams(location.search)}
function setQuery(values,{replace=false}={}){const p=query();Object.entries(values).forEach(([k,v])=>{if(v===undefined||v===null||v==='')p.delete(k);else p.set(k,String(v))});const url=`${location.pathname}${p.toString()?`?${p}`:''}`;(replace?history.replaceState:history.pushState).call(history,{},'',url)}
function pageButtons(meta,onPage){const {page=1,pages=0,total=0,page_size=50}=meta;if(!pages)return`<div class="pager"><span class="pager-info">共 0 条</span></div>`;const start=Math.max(1,page-2),end=Math.min(pages,start+4);let b=`<button class="page-btn" ${page<=1?'disabled':''} data-p="${page-1}">‹</button>`;for(let i=start;i<=end;i++)b+=`<button class="page-btn ${i===page?'active':''}" data-p="${i}">${i}</button>`;b+=`<button class="page-btn" ${page>=pages?'disabled':''} data-p="${page+1}">›</button>`;setTimeout(()=>document.querySelectorAll('[data-p]').forEach(el=>el.onclick=()=>{const p=Number(el.dataset.p);if(p>=1&&p<=pages)onPage(p)}),0);const from=(page-1)*page_size+1,to=Math.min(total,page*page_size);return`<div class="pager"><span class="pager-info">共 ${num(total)} 条 · 第 ${num(from)}–${num(to)} 条</span><div class="pager-controls">${b}</div></div>`}
function channels(values,max=3){const list=(values||[]).slice(0,max);if(!list.length)return'<span class="text-muted">—</span>';return`<div class="channel-stack">${list.map(v=>`<span>${esc(v)}</span>`).join('')}${(values||[]).length>max?`<span class="text-muted">+${(values||[]).length-max}</span>`:''}</div>`}
function error(target,e,prefix='加载失败'){const el=typeof target==='string'?document.querySelector(target):target;if(el)el.innerHTML=`<div class="error-box">${esc(prefix)}：${esc(e?.message||e)}</div>`}
function empty(message='暂无数据'){return`<div class="empty"><div class="empty-orbit">M</div>${esc(message)}</div>`}
function openDrawer(html){let bg=document.getElementById('drawer-bg'),dr=document.getElementById('drawer');if(!bg){bg=document.createElement('div');bg.id='drawer-bg';bg.className='drawer-backdrop';bg.onclick=closeDrawer;dr=document.createElement('aside');dr.id='drawer';dr.className='drawer';document.body.append(bg,dr)}dr.innerHTML=html;requestAnimationFrame(()=>{bg.classList.add('open');dr.classList.add('open')})}
function closeDrawer(){document.getElementById('drawer-bg')?.classList.remove('open');document.getElementById('drawer')?.classList.remove('open')}
async function loadHealth(){try{const d=await api('/api/health');[['h-api',d.api],['h-pg',d.postgres],['h-ch',d.clickhouse]].forEach(([id,v])=>{const el=document.getElementById(id);if(el)el.className=`health-dot ${String(v).startsWith('ok')?'ok':'error'}`});const v=document.getElementById('engine-version');if(v)v.textContent=d.version||'—';const r=document.getElementById('last-refresh');if(r)r.textContent=new Date().toLocaleTimeString()}catch{}}
window.MOAdmin={shell,api,esc,num,bytes,time,duration,status,segment,query,setQuery,pageButtons,channels,error,empty,openDrawer,closeDrawer};
})();
