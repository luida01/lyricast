import { spawn } from "node:child_process";

export interface RunCommandOptions {
  cwd?: string;
  inheritOutput?: boolean;
}

export class CommandError extends Error {
  readonly command: string;
  readonly exitCode: number | undefined;
  readonly spawnError: NodeJS.ErrnoException | undefined;

  constructor(
    message: string,
    command: string,
    exitCode?: number,
    spawnError?: NodeJS.ErrnoException,
  ) {
    super(message);
    this.name = "CommandError";
    this.command = command;
    this.exitCode = exitCode;
    this.spawnError = spawnError;
  }
}

export function runCommand(
  command: string,
  args: string[],
  options: RunCommandOptions = {},
): Promise<{ stdout: string; stderr: string }> {
  const commandLine = [command, ...args].join(" ");

  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      windowsHide: true,
      stdio: options.inheritOutput ? "inherit" : ["ignore", "pipe", "pipe"],
    });

    if (options.inheritOutput) {
      child.once("error", (error: NodeJS.ErrnoException) => {
        reject(new CommandError(`Could not start: ${commandLine}`, commandLine, undefined, error));
      });
      child.once("close", (exitCode) => {
        if (exitCode === 0) {
          resolve({ stdout: "", stderr: "" });
          return;
        }

        reject(new CommandError(
          `Command failed with exit code ${exitCode ?? "unknown"}: ${commandLine}`,
          commandLine,
          exitCode ?? undefined,
        ));
      });
      return;
    }

    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk: Buffer | string) => {
      stdout += chunk.toString();
    });
    child.stderr?.on("data", (chunk: Buffer | string) => {
      stderr += chunk.toString();
    });

    child.once("error", (error: NodeJS.ErrnoException) => {
      reject(new CommandError(`Could not start: ${commandLine}`, commandLine, undefined, error));
    });
    child.once("close", (exitCode) => {
      if (exitCode === 0) {
        resolve({ stdout, stderr });
        return;
      }

      const detail = stderr.trim() || `exit code ${exitCode}`;
      reject(new CommandError(`Command failed: ${commandLine}\n${detail}`, commandLine, exitCode ?? undefined));
    });
  });
}
