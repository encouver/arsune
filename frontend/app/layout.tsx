import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Arsune — Website checks with evidence",
  description:
    "Check navigation, mobile layout, and page consistency. See the evidence behind each finding.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
