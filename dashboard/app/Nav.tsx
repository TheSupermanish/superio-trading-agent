"use client";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const PAGES = [
  { href: "/", label: "book" },
  { href: "/chart", label: "chart" },
  { href: "/fleet", label: "fleet" },
];

export default function Nav({ here }: { here: string }) {
  return (
    <nav className="nav">
      {PAGES.map((p) => (
        <a key={p.href} href={`${BASE}${p.href}`} className={p.href === here ? "on" : ""}>
          {p.label}
        </a>
      ))}
    </nav>
  );
}
