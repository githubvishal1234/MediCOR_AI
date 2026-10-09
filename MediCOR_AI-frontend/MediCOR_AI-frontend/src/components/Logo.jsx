export default function Logo({ variant = "full", className = "", light = false }) {
  const mark = (
    <svg width="36" height="36" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="mv-grad" x1="4" y1="4" x2="36" y2="36" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0f172a" />
          <stop offset="1" stopColor="#0ea5b7" />
        </linearGradient>
      </defs>
      <path
        d="M15 4H25V13H34V23H25V36H15V23H6V13H15V4Z"
        stroke="url(#mv-grad)"
        strokeWidth="2.4"
        strokeLinejoin="round"
        fill="none"
      />
      <path
        d="M4 18.5H12L14.5 12L18 25L21 15L23.5 18.5H36"
        stroke="url(#mv-grad)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );

  if (variant === "mark") return <div className={className}>{mark}</div>;

  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      {mark}
      <span className={`font-display font-extrabold leading-none tracking-tight ${light ? "text-white" : "text-navy dark:text-white"}`}>
        <span className={light ? "text-white" : "text-navy dark:text-white"}>MediCOR</span>{" "}
        <span className="text-logo">AI</span>
      </span>
    </div>
  );
}