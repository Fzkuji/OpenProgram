"use client";
import type { DocumentController } from "@/lib/files/document-controller";
import { assertEncodedRaster, validateRasterDecoded } from "./raster-format";

type Crop = { left: number; top: number; width: number; height: number };
type TuiEditor = {
  rotate(degree: number): Promise<unknown>; crop(options: Crop): Promise<unknown>;
  addText(text: string, options?: Record<string, unknown>): Promise<unknown>;
  addShape(type: string, options?: Record<string, unknown>): Promise<unknown>;
  undo(): Promise<unknown>; redo(): Promise<unknown>;
  toDataURL(options?: { format?: string; quality?: number }): string; destroy(): void;
  loadImageFromURL(url: string, name: string): Promise<unknown>; getCanvasSize(): { width: number; height: number };
  clearUndoStack(): void; clearRedoStack(): void; getCropzoneRect(): Crop;
  startDrawingMode(mode: string, options?: Record<string, unknown>): unknown; stopDrawingMode(): void;
  on(event: string, handler: () => void): void; off(event: string, handler: () => void): void;
};
export interface RasterEditorInstance {
  save(): Promise<File>; flushPendingSaves(): Promise<void>;
  setReadonly(readonly: boolean): void; setInputEnabled(enabled: boolean): void;
  destroy(): Promise<void>; getState(): { dirty: boolean; readonly: boolean; destroyed: boolean; status: string };
  rotate(): Promise<void>; crop(options: Crop): Promise<void>;
  addText(text: string): Promise<void>; addShape(type: string): Promise<void>;
  draw(): Promise<void>; startCrop(): Promise<void>; applyCrop(): Promise<void>; cancelTool(): Promise<void>; undo(): Promise<void>; redo(): Promise<void>;
}
type TuiModule = { default: new (element: HTMLElement, options: Record<string, unknown>) => TuiEditor };
let modulePromise: Promise<TuiModule> | null = null;
async function loadTui(): Promise<TuiModule> {
  modulePromise ??= (import("tui-image-editor") as unknown as Promise<TuiModule>).catch(error=>{modulePromise=null;throw error;});
  return modulePromise;
}
const nextFrame = () => new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));
export async function createBoundRasterEditor(options: {
  container: HTMLElement; controller: DocumentController; bytes: Blob; fileName: string; readonly?: boolean;
  generation?: number; signal?: AbortSignal;
}): Promise<RasterEditorInstance> {
  const generation=options.generation ?? options.controller.getState().editorRevision;
  options.signal?.throwIfAborted();
  const checked=await validateRasterDecoded(options.bytes);
  const extension=options.fileName.toLowerCase().split(".").pop();
  if ((extension === "jpg" ? "jpeg" : extension) !== checked.format)
    throw new Error("UNSUPPORTED_IMAGE: file extension and raster encoding differ.");
  const { default: Editor }=await loadTui();
  options.signal?.throwIfAborted();
  const editor=new Editor(options.container,{includeUI:false,usageStatistics:false,cssMaxWidth:1600,cssMaxHeight:1200,selectionStyle:{cornerSize:12}});
  const sourceUrl=URL.createObjectURL(options.bytes);
  try { await editor.loadImageFromURL(sourceUrl,options.fileName);options.signal?.throwIfAborted();editor.clearUndoStack();editor.clearRedoStack(); }
  catch(error) {editor.destroy();throw error;}
  finally {URL.revokeObjectURL(sourceUrl);}

  let readonly=Boolean(options.readonly), enabled=true, destroyed=false, dirty=false, revision=0;
  let commands: Promise<unknown>=Promise.resolve(), saves: Promise<unknown>=Promise.resolve();
  let detach: (()=>void) | undefined;
  const syncInput=()=>{options.container.toggleAttribute("inert",!enabled || readonly || destroyed);};
  const markDirty=()=>{if(destroyed)return;revision++;dirty=true;options.controller.markRichEditorDirty(true,instance);};
  const run=(operation:()=>Promise<unknown>, stopDrawing=true):Promise<void>=>{
    if(readonly || !enabled || destroyed)return Promise.reject(new Error("Raster editor is read-only."));
    const task=commands.then(async()=>{if(destroyed)throw new Error("The image editor was closed.");if(stopDrawing)editor.stopDrawingMode();await operation();});
    commands=task.catch(()=>undefined);return task;
  };
  const exportFile=async()=>{
    await commands;await nextFrame();await nextFrame();
    if(destroyed)throw new Error("The image editor was closed.");
    const captured=revision;
    const url=editor.toDataURL({format:checked.format,quality:0.95});
    const blob=await (await fetch(url)).blob();
    await assertEncodedRaster(blob,checked.format);
    const file=new File([blob],options.fileName,{type:checked.mime});
    if(destroyed)throw new Error("The image editor was closed.");
    if(!options.readonly)await options.controller.stageRichExport(file,generation);
    dirty=revision !== captured;
    return file;
  };
  const instance: RasterEditorInstance={
    save(){const task=saves.then(exportFile);saves=task.catch(()=>undefined);return task;},
    async flushPendingSaves(){let last;do{last=saves;await last;await commands;}while(last !== saves);},
    setReadonly(value){readonly=value;syncInput();},
    setInputEnabled(value){enabled=value;syncInput();},
    async destroy(){
      if(destroyed)return;destroyed=true;syncInput();
      editor.off("undoStackChanged",markDirty);editor.off("redoStackChanged",markDirty);
      await commands;await saves;
      editor.destroy();detach?.();detach=undefined;
    },
    getState(){return {dirty,readonly,destroyed,status:destroyed ? "destroyed" : "ready"};},
    rotate:()=>run(()=>editor.rotate(90)),
    crop:(value)=>run(async()=>{
      const size=editor.getCanvasSize();
      if(![value.left,value.top,value.width,value.height].every(Number.isFinite) || value.left<0 || value.top<0 || value.width<1 || value.height<1 || value.left+value.width>size.width || value.top+value.height>size.height)
        throw new Error("The crop must be inside the current image.");
      editor.stopDrawingMode();
      await editor.crop(value);
    },false),
    addText:(text)=>run(()=>editor.addText(text,{styles:{fontSize:24,fill:"#111111"}})),
    addShape:(type)=>run(()=>editor.addShape(type,{width:60,height:40,fill:"#ff00ff",stroke:"#ff00ff",strokeWidth:2})),
    draw:()=>run(async()=>{editor.startDrawingMode("FREE_DRAWING",{width:4,color:"#ff00ff"});}),
    startCrop:()=>run(async()=>{editor.startDrawingMode("CROPPER");}),
    applyCrop:()=>{const rect=editor.getCropzoneRect();return instance.crop({...rect});},
    cancelTool:()=>run(async()=>{}),
    undo:()=>run(()=>editor.undo()),redo:()=>run(()=>editor.redo()),
  };
  editor.on("undoStackChanged",markDirty);editor.on("redoStackChanged",markDirty);
  try {if(!options.readonly)detach=options.controller.attachRichEditor(instance,generation);syncInput();return instance;}
  catch(error){await instance.destroy();throw error;}
}
