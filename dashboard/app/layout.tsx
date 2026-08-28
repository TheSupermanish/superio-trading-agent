import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Superio — autonomous options agent",
  description:
    "Live P&L, open structures, and the full decision trail of an autonomous defined-risk options agent trading on Alpaca.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
