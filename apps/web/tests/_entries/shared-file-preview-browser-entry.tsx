import React from 'react';
import {createRoot} from 'react-dom/client';
import {PersistentFilePanes} from '../../components/center-tabs/files/persistent-file-panes';
import {useCenterTabs} from '../../lib/tabs/center-tabs-store';
localStorage.setItem('agentic_locale','en');
const chats=[{id:'s:a',kind:'session' as const,sessionId:'a',title:'A'},{id:'s:b',kind:'session' as const,sessionId:'b',title:'B'}];
useCenterTabs.setState({tabs:chats,groups:[],activeId:'s:a'});
const showOwner=(sid:string)=>{useCenterTabs.setState({activeId:`s:${sid}`});return useCenterTabs.getState().openFilePreview(sid,{projectId:'p',path:'report.pdf'},true);};
Object.assign(window,{showOwner});showOwner('a');
function Fixture(){
 const [all,setAll]=React.useState(false);
 Object.assign(window,{showBoth:()=>setAll(true)});
 const tabs=useCenterTabs(s=>s.tabs),groups=useCenterTabs(s=>s.groups),activeId=useCenterTabs(s=>s.activeId);
 const ids=all ? tabs.filter(t=>t.kind==='file').map(t=>t.id) : groups.find(g=>g.memberIds.includes(activeId!))?.visibleIds.filter(id=>tabs.some(t=>t.id===id&&t.kind==='file'))??[];
 return <PersistentFilePanes tabs={tabs} activeFileIds={new Set(ids)} layouts={new Map(ids.map(id=>[id,{className:'pane',style:{height:'700px',width:'700px',position:'relative' as const,overflow:'hidden' as const}}]))}/>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
