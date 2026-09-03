import type { Metadata } from "next";

import { Nav } from "@/components/Nav";
import { getHealth } from "@/lib/api";

import "./globals.css";

export const metadata: Metadata = {
  title: "TraceLens",
  description:
    "Failure forensics for multi-stage AI pipelines: find the first point of divergence and the evidence for it.",
};

// The shell shows live API status, so it must not be cached.
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const health = await getHealth();

  return (
    <html lang="en">
      <body>
        <div className="shell">
          <aside className="sidebar">
            <div className="brand">
              <span className="brand-name">TraceLens</span>
              <span className="brand-version">
                {health.ok ? `v${health.data.version}` : "offline"}
              </span>
            </div>

            <Nav />

            <div className="sidebar-footer">
              <span className={`badge ${health.ok ? "badge-ok" : "badge-error"}`}>
                <span className="dot" aria-hidden="true" />
                {health.ok ? `API ${health.data.status}` : "API unreachable"}
              </span>
              {health.ok ? <span>storage: {health.data.database}</span> : null}
            </div>
          </aside>

          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
