export function installSensitiveLogFilters(): void {
  const originalInfo = console.info.bind(console);
  console.info = (...args: unknown[]) => {
    if (args[0] === "Closing session:" || args[0] === "Session already closed") return;
    originalInfo(...args);
  };
}
