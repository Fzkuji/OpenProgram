const test = require('node:test');
const assert = require('node:assert/strict');
const {createMenus} = require('../main/windows/menus');
const geometry = require('../main/windows/menu-geometry');

function fixture() {
  const views = [];
  const attached = new Set();
  const ctx = {id:'owner', win:{
    isDestroyed:()=>false, getContentBounds:()=>({width:1000,height:700}),
    contentView:{addChildView:v=>attached.add(v),removeChildView:v=>attached.delete(v)},
    webContents:{send(){}},
  }};
  class WebContentsView {
    constructor() {
      this.loads = []; this.messages = []; this.closed = false; this.events = {};
      this.webContents = {
        isDestroyed:()=>this.closed, isLoadingMainFrame:()=>false,
        close:()=>{this.closed=true;}, setZoomFactor:z=>{this.zoom=z;},
        loadURL:url=>{this.loads.push(url);return Promise.resolve();},
        send:(...args)=>this.messages.push(args), focus:()=>{},
        on:(name,handler)=>{this.events[name]=handler;},
      };
      views.push(this);
    }
    setBounds(bounds) {this.bounds=bounds;}
    setBackgroundColor() {}
  }
  const menus=createMenus({
    ...geometry, WebContentsView, path:require('node:path'),__dirname:'/app',
    MAIN_MENU_GUTTER:24,MAIN_MENU_HEIGHT:88,MAIN_MENU_WIDTH:224,
    CONTEXT_MENU_CHROME:16,CONTEXT_MENU_ROW_HEIGHT:24,CONTEXT_MENU_WIDTH:200,
    MENU_THEME_ID_SET:new Set(['dark']),START_URL:'http://127.0.0.1:18100',WEB_PORT:18100,
    clearTimeout,setTimeout,windows:new Map([[ctx.id,ctx]]),contextForSender:()=>null,
  });
  const open=(x=10)=>menus.openMainMenu(ctx,{cascade:true,items:[{id:'bookmark:one',label:'One'}],anchor:{x,y:80,vw:1000,vh:700},width:280,theme:'dark'});
  return {ctx,menus,views,attached,open};
}

test('bookmark menu close/reopen reuses the document and rejects detached senders', async()=>{
  const {ctx,menus,views,attached,open}=fixture();
  try {
    open(); await Promise.resolve();
    const first=ctx.mainMenuView;
    menus.closeMainMenu(ctx);
    assert.equal(attached.size,0);
    assert.equal(menus.contextForMenuSender({sender:first.webContents}),null);
    assert.equal(first.closed,false,'Closing a bookmark menu must keep its decoded icons');
    open(200);
    assert.equal(ctx.mainMenuView,first);
    assert.equal(first.loads.length,1);
    assert.equal(first.messages.at(-1)[0],'main-menu:update');
    assert.equal(views.length,1);
    assert.equal(attached.size,1);
    assert.equal(menus.contextForMenuSender({sender:first.webContents}),ctx);
    menus.closeMainMenu(ctx);
    menus.openMainMenu(ctx,{});
    const ordinary=ctx.mainMenuView;
    menus.closeMainMenu(ctx);
    assert.equal(ordinary.closed,true,'Ordinary menus retain existing disposal');
    open();
    assert.equal(ctx.mainMenuView,first);
  } finally {
    menus.closeMainMenu(ctx,true);
  }
  assert.ok(views.every(view=>view.closed),'Owner cleanup closes active and cached views');
});
