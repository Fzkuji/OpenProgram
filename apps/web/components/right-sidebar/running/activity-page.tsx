"use client";

/** One page per level-1 click: the top line, the head, and one track that
 *  holds the words on the left and the same rows against time on the right.
 *  Width changes write CSS variables on the element; React hears about them
 *  only when the Timeline button appears or disappears. */
import { useCallback, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ActivityPageFrameProps, ActivityPageTarget, ActivityPanelContext, Translate } from "./activity-page-types";
import { computeActivityGeometry, geometryVariables } from "./activity-page-geometry";
import { ActivityCell, ActivityDetail, ActivityNote, ActivityPanelReact, ActivityRowStoreReact, createActivityRowStore, useActivityPanel } from "./activity-page-row";
import { useActivityPageKeys } from "./use-activity-page-keys";
import { observeSwipe } from "./sideways";
import { AgentPage } from "./agent-page";
import { ProgramPage } from "./program-page";
import { CommandPage } from "./command-page";
import { ScheduledTaskPage } from "./scheduled-task-page";
import { OpenWorkPage } from "./open-work-page";
import styles from "./activity-page.module.css";

const motion = (): ScrollBehavior => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";

export function ActivityPageFrame({ kind, title, status, tone, headExtra, axis, children }: ActivityPageFrameProps) {
  const context = useActivityPanel();
  const { text } = context;
  const [store] = useState(createActivityRowStore);
  const root = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const [timeline, setTimeline] = useState({ shown: false, atEnd: false });
  const hasAxis = axis !== null;
  const shownTone = tone === "muted" || tone === "ok" ? undefined : tone;

  const syncTimeline = useCallback(() => {
    const list = scroller.current;
    if (!list) return;
    const max = list.scrollWidth - list.clientWidth;
    const shown = max >= 48;
    const atEnd = list.scrollLeft > max / 2;
    setTimeline(previous => previous.shown === shown && previous.atEnd === atEnd ? previous : { shown, atEnd });
  }, []);

  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const apply = () => {
      const width = element.clientWidth;
      if (width <= 0) return;
      const computed = computeActivityGeometry(width);
      const geometry = hasAxis ? computed : { ...computed, L: computed.V, R: 0, track: computed.V, P: 0, both: false };
      for (const [name, value] of Object.entries(geometryVariables(geometry))) element.style.setProperty(name, value);
      element.dataset.both = geometry.both ? "1" : "0";
      syncTimeline();
    };
    apply();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(apply);
    observer.observe(element);
    return () => observer.disconnect();
  }, [hasAxis, syncTimeline]);

  useLayoutEffect(() => { back.current?.focus({ preventScroll: true }); }, []);
  useActivityPageKeys(root, scroller, context.close);
  // A swipe towards the overview drags the page aside once the track is at its left end.
  const pull = context.pull;
  useLayoutEffect(() => {
    const list = scroller.current;
    if (!list || !pull) return;
    return observeSwipe(list, {
      begin: direction => direction < 0 && list.scrollLeft < 1,
      move: moved => pull.move(Math.max(0, -moved)),
      end: (moved, speed) => pull.end(Math.max(0, -moved), -speed),
    }, false);
  }, [pull]);

  const flip = () => {
    const list = scroller.current;
    if (!list) return;
    const max = list.scrollWidth - list.clientWidth;
    list.scrollTo({ left: list.scrollLeft > max / 2 ? 0 : max, behavior: motion() });
  };
  const glyph = typeof kind === "string" ? null : kind;
  const trackStyle = axis ? { "--g": `calc(var(--P) / ${axis.divisions})` } as CSSProperties : undefined;

  return <ActivityRowStoreReact.Provider value={store}>
      <div ref={root} className={`${styles.root} ${styles.fade}`} data-timeline={hasAxis ? "1" : "0"} data-both="0">
        <div className={styles.top}>
          <Button ref={back} type="button" variant="ghost" className={styles.bk} onClick={context.close}><ChevronLeft size={14} strokeWidth={1.8} aria-hidden="true" />{text("Activity", "运行记录")}</Button>
          {hasAxis && timeline.shown ? <Button type="button" variant="ghost" className={styles.tl} onClick={flip}>
            {timeline.atEnd ? <><ChevronLeft size={14} strokeWidth={1.8} aria-hidden="true" />{text("List", "列表")}</> : <>{text("Timeline", "时间线")}<ChevronRight size={14} strokeWidth={1.8} aria-hidden="true" /></>}
          </Button> : null}
        </div>
        <div ref={scroller} className={styles.sc} onScroll={syncTimeline}>
          <div className={styles.track} style={trackStyle}>
            <div className={`${styles.ln} ${styles.nor}`}>
              <div className={`${styles.lc} ${styles.snap}`}>
                <ActivityCell glyph={glyph} title={title} status={status} tone={shownTone} extra={headExtra} />
              </div>
              <span className={styles.gp} />
              <div className={`${styles.rc} ${styles.snap}`}></div>
            </div>
            {context.actionError ? <div role="alert"><ActivityDetail><ActivityNote tone="err">{context.actionError}</ActivityNote></ActivityDetail></div> : null}
            {children}
          </div>
        </div>
      </div>
  </ActivityRowStoreReact.Provider>;
}

/** The item behind a page has gone; the page stays until the reader leaves.
 *  An element, not a component: the frame already on screen must stay the same
 *  instance, because a new frame takes the focus and a poll must not. */
export function activityGoneFrame(text: Translate) {
  return <ActivityPageFrame kind={null} title={text("No longer here", "已不在这里")} axis={null}>{null}</ActivityPageFrame>;
}

function PageForTarget({ target, context }: { target: ActivityPageTarget; context: ActivityPanelContext }) {
  switch (target.kind) {
    case "agent": return <AgentPage target={target} context={context} />;
    case "program": return <ProgramPage target={target} context={context} />;
    case "command": return <CommandPage target={target} context={context} />;
    case "routine": return <ScheduledTaskPage target={target} context={context} />;
    case "work": return <OpenWorkPage target={target} context={context} />;
  }
}

export function ActivityPage({ target, context }: { target: ActivityPageTarget; context: ActivityPanelContext }) {
  return <ActivityPanelReact.Provider value={context}>
    <PageForTarget target={target} context={context} />
  </ActivityPanelReact.Provider>;
}
