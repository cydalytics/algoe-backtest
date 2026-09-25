import type { ReactNode } from "react";

interface HeaderProps {
  title: string;
  subtitle?: string;
  children?: ReactNode;
}

export function Header({ title, subtitle, children }: HeaderProps) {
  return (
    <header className="border-b border-border/80">
      <div className="flex items-end justify-between px-5 py-3.5">
        <div>
          <h1 className="text-[15px] font-semibold tracking-[0.08em] uppercase">
            {title}
          </h1>
          {subtitle && (
            <p className="mt-0.5 text-[11px] text-muted-foreground">{subtitle}</p>
          )}
        </div>
        {children && <div className="flex items-center gap-2">{children}</div>}
      </div>
    </header>
  );
}
