// Order assistant endpoint.
import { execSync } from "node:child_process";
import OpenAI from "openai";
import { trace } from "@opentelemetry/api";
import type { Request, Response } from "express";

const tracer = trace.getTracer("order-assistant");
const MODEL = process.env.ASSISTANT_MODEL!;
const FALLBACK_MODEL = process.env.ASSISTANT_FALLBACK_MODEL!;

// The SDK reads the key from the OPENAI_API_KEY environment variable.
const client = new OpenAI({ timeout: 20_000, maxRetries: 3 });

const tools: OpenAI.Chat.Completions.ChatCompletionTool[] = [
  {
    type: "function",
    function: {
      name: "lookup_order",
      description: "Look up the status of an order by its ID.",
      parameters: {
        type: "object",
        properties: { orderId: { type: "string" } },
        required: ["orderId"],
      },
    },
  },
];

function lookupOrder(orderId: string): string {
  return execSync(`./bin/order-status ${orderId}`).toString();
}

export async function answer(req: Request, res: Response) {
  const { question, customerName, accountNotes } = req.body;
  const system = `You are the order assistant for ${customerName}. Account notes: ${accountNotes}. Answer in two sentences.`;

  for (const model of [MODEL, FALLBACK_MODEL]) {
    const span = tracer.startSpan("llm.answer", { attributes: { "llm.model": model } });
    try {
      const completion = await client.chat.completions.create({
        model,
        max_completion_tokens: 300,
        tools,
        messages: [
          { role: "system", content: system },
          { role: "user", content: question },
        ],
      });
      span.setAttribute("llm.prompt_tokens", completion.usage?.prompt_tokens ?? 0);
      span.setAttribute("llm.completion_tokens", completion.usage?.completion_tokens ?? 0);
      const message = completion.choices[0].message;
      const call = message.tool_calls?.[0];
      if (call && call.type === "function" && call.function.name === "lookup_order") {
        const { orderId } = JSON.parse(call.function.arguments);
        res.json({ answer: lookupOrder(orderId) });
        return;
      }
      res.json({ answer: message.content ?? "" });
      return;
    } catch (err) {
      span.recordException(err as Error);
      if (err instanceof OpenAI.APIError && (err.status === 429 || (err.status ?? 0) >= 500)) {
        // Rate limited or overloaded, after the SDK's own retries.
        console.warn(`model ${model} unavailable (${err.status}); trying the fallback model`);
        continue;
      }
      console.error("answer failed", err);
      break;
    } finally {
      span.end();
    }
  }
  res.status(503).json({ error: "The assistant is not available right now. Please try again in a few minutes." });
}
