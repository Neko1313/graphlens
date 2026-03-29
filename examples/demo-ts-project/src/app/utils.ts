import * as path from "path";
import * as os from "os";

export function greet(name: string): string {
  return `Hello, ${name}!`;
}

export function resolvePath(...parts: string[]): string {
  return path.resolve(...parts);
}

export async function getHostInfo(): Promise<{ hostname: string; platform: string }> {
  return {
    hostname: os.hostname(),
    platform: os.platform(),
  };
}
