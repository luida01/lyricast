import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";

export async function ask(question: string): Promise<string> {
  const readline = createInterface({ input, output });
  try {
    return (await readline.question(question)).trim();
  } finally {
    readline.close();
  }
}
