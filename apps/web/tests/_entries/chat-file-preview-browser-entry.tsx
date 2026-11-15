import React from 'react';
import {createRoot} from 'react-dom/client';
import {AssistantBubble} from '../../components/chat/messages/bubbles/assistant-bubble';
import {PersistentFilePanes} from '../../components/center-tabs/files/persistent-file-panes';
import {useCenterTabs} from '../../lib/tabs/center-tabs-store';
import {useSessionStore} from '../../lib/session-store';
import {applyChatWsMessage} from '../../lib/net/ws/chat-stream';
import {setSocket} from '../../lib/runtime-bridge/state';
import {normalizeCenterTabLayout} from '../../lib/tabs/center-tab-groups';
localStorage.setItem('agentic_locale','en');
class ProjectSocket extends EventTarget {
  readyState=WebSocket.OPEN;
  send(raw:string){const frame=JSON.parse(raw);queueMicrotask(()=>this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'projects_list',data:{...frame,session_id:frame.session_id,request_id:frame.request_id,action:'list_projects',projects:[{id:'p',name:'Project',path:'/project',is_default:true}],current_project_id:'p'}})})));}
}
setSocket(new ProjectSocket() as unknown as WebSocket);
useSessionStore.setState({currentSessionId:'s',wsStatus:'open'});
const chat={id:'s:s',kind:'session' as const,sessionId:'s',title:'Chat'};
useCenterTabs.setState({tabs:[chat],groups:[],activeId:chat.id});
const params=new URLSearchParams(location.search);
if(params.has('manual')){
 const tabs=[chat,{id:'manual',kind:'file' as const,title:'Manual',projectId:'p',path:'manual.md'}];
 const layout=normalizeCenterTabLayout({tabIds:tabs.map(t=>t.id),groups:[{id:'manual-group',memberIds:tabs.map(t=>t.id),visibleIds:tabs.map(t=>t.id),focusedId:chat.id}]});
 useCenterTabs.setState({tabs,groups:layout.groups});
}
let sequence=0;
const complete=(name='report.md')=>{
 const id=`u${++sequence}`;
 applyChatWsMessage({type:'chat_ack',data:{session_id:'s',msg_id:id}});
 applyChatWsMessage({type:'chat_response',data:{type:'result',session_id:'s',msg_id:id,content:`Created [${name}](/project/${name}).`}});
};
Object.assign(window,{complete,layout:()=>useCenterTabs.getState()});
function Fixture(){
 const msg=useSessionStore(s=>Object.values(s.rowsByView.s??{}).filter(msg=>msg.role==='assistant').at(-1));
 const tabs=useCenterTabs(s=>s.tabs),groups=useCenterTabs(s=>s.groups);
 const ids=groups[0]?.visibleIds.filter(id=>id!==chat.id)??[];
 const layouts=new Map(ids.map(id=>[id,{className:'file-pane',style:{width:'50%',height:'100%'}}]));
 return <div style={{display:'flex',height:'90vh'}}><div style={{width:ids.length?'50%':'100%'}}>{msg&&<AssistantBubble msg={msg} sessionIdOverride="s"/>}</div><PersistentFilePanes tabs={tabs} activeFileIds={new Set(ids)} layouts={layouts}/></div>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
