import type { Metadata } from "next";
import { Manrope, Space_Mono } from "next/font/google";
import "./globals.css";

const manrope = Manrope({ variable: "--font-manrope", subsets: ["cyrillic", "latin"] });
const spaceMono = Space_Mono({ variable: "--font-mono", subsets: ["latin"], weight: ["400", "700"] });

export const metadata: Metadata = {
  metadataBase: new URL("https://bogdasovandrej.github.io/xg-edge/"),
  title: "xg-edge — честные футбольные вероятности",
  description: "Live-прогнозы ЧМ и Лиги чемпионов с открытой неопределённостью и проспективной проверкой CLV.",
  openGraph: {
    title: "xg-edge — Рейтинг недели",
    description: "Очередь разбора футбольных матчей на основе качества данных, свежести линии и сценарной хрупкости.",
    images: [{ url: "/xg-edge/og.png", width: 1732, height: 909, alt: "xg-edge — Рейтинг недели" }],
    type: "website",
    locale: "ru_RU",
  },
  twitter: {
    card: "summary_large_image",
    title: "xg-edge — Рейтинг недели",
    description: "Строгий исследовательский радар будущих матчей.",
    images: ["/xg-edge/og.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body className={`${manrope.variable} ${spaceMono.variable}`}>{children}</body>
    </html>
  );
}
