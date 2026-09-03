import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Alphaca — autonomous options agent on Alpaca",
  description:
    "Live P&L, defined-risk options structures, and the multi-agent cognitive decision trail of Alphaca on Alpaca paper trading.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
