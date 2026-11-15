"""What fake sockets in the browser tests answer: turns and a window, as the server sends them."""
from __future__ import annotations


def history_wire_script() -> str:
    """JavaScript prepended to a test bundle.

    Tests build their pages as ``{messages, history}``. ``asWire`` turns one
    into turns and a window, with one row to a turn, so turn cursors are the
    row ids the tests already compute. ``seedWire`` installs one as the window
    a read would have left.
    """
    return """
import {registerSessionHistory as registerWire} from './lib/chat/history/session-history';
import {seedTurnWindow as seedTurnsWire} from './lib/runtime-bridge/session/session-history-loader';
const toWireTurns=rows=>rows.map(row=>({id:row.id,rows:[row]}));
const toWireWindow=h=>({head_id:h.head_id,revision:1,before:h.before??null,after:h.after??null,start:h.start,end:h.end,total:h.total});
function asWire({messages,history,...rest}){
  return {...rest,turns:toWireTurns(messages),window:toWireWindow(history)};
}
function seedWire(id,{messages,history}){
  seedTurnsWire(id,toWireTurns(messages),toWireWindow(history));registerWire(id,toWireWindow(history));
}
"""
