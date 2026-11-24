import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "./ui";

describe("Button", () => {
  it("stays disabled while busy even when the caller passes disabled={false}", () => {
    // The login form passes disabled={flag !== null}; a double tap must not send a second code request.
    render(<Button busy disabled={false}>Send code</Button>);
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("honours disabled when not busy", () => {
    render(<Button disabled>Send code</Button>);
    expect(screen.getByRole("button", { name: "Send code" })).toBeDisabled();
  });
});
