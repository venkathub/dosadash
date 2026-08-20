import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DosaDash — South Indian, served hot",
  description: "AI-native South Indian cloud kitchen",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-cream-100 font-sans text-ink-900 antialiased">
        {children}
      </body>
    </html>
  );
}
