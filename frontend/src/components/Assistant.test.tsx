import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownLite } from "./Assistant";

describe("MarkdownLite", () => {
  it("renders bold, lists and paragraphs without HTML injection", () => {
    const { container } = render(
      <MarkdownLite text={"You are **eligible** for:\n- Acme (P0)\n- Chipset\n\n<img src=x onerror=alert(1)>"} />,
    );
    expect(screen.getByText("eligible").tagName).toBe("STRONG");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x/)).toBeTruthy();
  });
});
