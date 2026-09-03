"use client";

import { useEffect, useState } from "react";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const PAGES = [
  { href: "/", label: "Book", icon: "📖" },
  { href: "/chart", label: "Tape", icon: "📊" },
  { href: "/intelligence", label: "Intelligence", icon: "🧠" },
  { href: "/fleet", label: "Fleet", icon: "🚢" },
];

export default function Nav({ here }: { here: string }) {
  const [etTime, setEtTime] = useState<string>("");
  const [isMarketOpen, setIsMarketOpen] = useState<boolean>(false);

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      // Format to America/New_York
      const formatter = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        hour: "numeric",
        minute: "numeric",
        second: "numeric",
        hour12: true,
        weekday: "short",
      });
      setEtTime(formatter.format(now) + " ET");

      // Check market hours (Mon-Fri 9:30 AM to 4:00 PM ET)
      const day = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short" }).format(now);
      const isWeekday = ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(day);
      const hourStr = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", hour12: false }).format(now);
      const minStr = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", minute: "numeric" }).format(now);
      const hour = parseInt(hourStr, 10);
      const minute = parseInt(minStr, 10);
      const totalMinutes = hour * 60 + minute;
      const open = isWeekday && totalMinutes >= 9 * 60 + 30 && totalMinutes < 16 * 60;
      setIsMarketOpen(open);
    };

    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <nav className="nav-container" style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", marginBottom: 20, gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        {/* Alphaca Logo Branding */}
        <a href={`${BASE}/`} style={{ display: "flex", alignItems: "center", gap: 10, textDecoration: "none" }}>
          <img
            src={`${BASE}/alphaca_icon.jpg`}
            alt="Chad Alphaca Mascot"
            style={{
              width: 38,
              height: 38,
              borderRadius: "50%",
              border: "1.5px solid var(--accent)",
              boxShadow: "0 0 12px rgba(16, 185, 129, 0.4)",
              objectFit: "cover",
            }}
          />
          <span style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.03em", color: "#f3f4f6" }}>
            alpha<span style={{ color: "var(--accent)" }}>ca</span>
          </span>
        </a>

        {/* Nav Links */}
        <div className="nav" style={{ margin: 0 }}>
          {PAGES.map((p) => {
            const isActive = p.href === here || (p.href !== "/" && here.startsWith(p.href));
            return (
              <a
                key={p.href}
                href={`${BASE}${p.href}`}
                className={isActive ? "on" : ""}
                style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
              >
                <span>{p.icon}</span>
                <span>{p.label}</span>
              </a>
            );
          })}
        </div>
      </div>

      {/* Market Status and Clock */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 11 }}>
        <span
          className="badge"
          style={{
            borderColor: "rgba(16, 185, 129, 0.4)",
            color: "var(--up)",
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            fontWeight: 700,
          }}
        >
          <span>⚡</span> CHAD ALPHA
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              backgroundColor: isMarketOpen ? "var(--up)" : "var(--muted)",
              boxShadow: isMarketOpen ? "0 0 8px var(--up)" : "none",
              display: "inline-block",
            }}
          />
          <span style={{ color: isMarketOpen ? "var(--up)" : "var(--muted)", fontWeight: 600, letterSpacing: "0.04em" }}>
            {isMarketOpen ? "MARKET OPEN" : "MARKET CLOSED"}
          </span>
        </div>
        {etTime && <span style={{ color: "var(--muted)" }}>{etTime}</span>}
      </div>
    </nav>
  );
}
