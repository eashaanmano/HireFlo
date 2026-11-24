import { describe, expect, it } from "vitest";
import { domainProblem } from "./email";

const D = "nitc.ac.in";
const FLAG = "Only @nitc.ac.in addresses can sign in.";

describe("domainProblem", () => {
  it("stays quiet until the domain is being typed", () => {
    expect(domainProblem("", D, false)).toBeNull();
    expect(domainProblem("eashaan_m260351cs", D, false)).toBeNull();
    expect(domainProblem("eashaan_m260351cs@", D, false)).toBeNull();
  });

  it("allows a domain that is still on its way to nitc.ac.in", () => {
    expect(domainProblem("x@ni", D, false)).toBeNull();
    expect(domainProblem("x@nitc", D, false)).toBeNull();
  });

  it("flags another domain from its first wrong letter", () => {
    expect(domainProblem("x@g", D, false)).toBe(FLAG);
    expect(domainProblem("someone@gmail.com", D, false)).toBe(FLAG);
    expect(domainProblem("x@nitc.ac.in.evil.com", D, false)).toBe(FLAG);
  });

  it("flags an unfinished domain once the form is sent", () => {
    expect(domainProblem("x@nitc", D, true)).toBe(FLAG);
    expect(domainProblem("x", D, true)).toBe(FLAG);
  });

  it("ignores capitals and spaces", () => {
    expect(domainProblem(" Eashaan_M260351CS@NITC.AC.IN ", D, true)).toBeNull();
  });

  it("allows anything when no domain is enforced", () => {
    expect(domainProblem("someone@gmail.com", "", true)).toBeNull();
  });
});
