"use client";
import dynamic from "next/dynamic";
const ApplicationsPage = dynamic(() => import("@/components/applications/applications-page").then(m => m.ApplicationsPage), { ssr: false });
export default function Page() { return <ApplicationsPage />; }
