"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/traces", label: "Traces" },
  { href: "/failures", label: "Failures" },
  { href: "/benchmarks", label: "Benchmarks" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="nav" aria-label="Primary">
      <span className="nav-heading">Navigate</span>
      {LINKS.map((link) => {
        // Only "/" needs an exact match; the rest own their subtrees, so a
        // trace detail page still highlights Traces.
        const active =
          link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            className="nav-link"
            aria-current={active ? "page" : undefined}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
