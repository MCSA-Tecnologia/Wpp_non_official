import { ApiClient } from "./api-client.js";
import { installSensitiveLogFilters } from "./sensitive-logs.js";
import { Supervisor } from "./supervisor.js";

installSensitiveLogFilters();
const supervisor = new Supervisor(new ApiClient());

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void supervisor.stop().finally(() => process.exit(0));
  });
}

await supervisor.run();
