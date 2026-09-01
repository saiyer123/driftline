"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  ["/", "Overview"],
  ["/positions", "Positions"],
  ["/orders", "Orders & fills"],
  ["/attribution", "Attribution"],
  ["/journal", "Journal"],
];

export default function Nav() {
  const path = usePathname();
  return (
    <nav className="sidebar">
      <div className="brand">
        driftline<span className="mode">PAPER</span>
      </div>
      {LINKS.map(([href, label]) => (
        <Link key={href} href={href} className={`nav-item${path === href ? " active" : ""}`}>
          {label}
        </Link>
      ))}
    </nav>
  );
}
