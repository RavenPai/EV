import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NewDeliveryInput } from "../types";

const mocks = vi.hoisted(() => ({
  getUser: vi.fn(),
  profileRoleSingle: vi.fn(),
  requesterProfileMaybeSingle: vi.fn(),
  deliveryRowsOrder: vi.fn(),
  robotRowsOrder: vi.fn(),
  deliveryInsert: vi.fn(),
  deliveryInsertSingle: vi.fn(),
  channelOn: vi.fn(),
  channelSubscribe: vi.fn(),
  removeChannel: vi.fn(),
}));

vi.mock("../lib/supabase", () => {
  const channel = {
    on: (...args: unknown[]) => {
      mocks.channelOn(...args);
      return channel;
    },
    subscribe: () => {
      mocks.channelSubscribe();
      return channel;
    },
  };

  return {
    cloudEnabled: true,
    backendMode: "Supabase cloud",
    supabase: {
      auth: { getUser: mocks.getUser },
      from: (table: string) => {
        if (table === "profiles") {
          return {
            select: (columns: string) => ({
              eq: () => columns === "role"
                ? { single: mocks.profileRoleSingle }
                : { maybeSingle: mocks.requesterProfileMaybeSingle },
            }),
          };
        }
        if (table === "deliveries") {
          return {
            select: () => ({ order: mocks.deliveryRowsOrder }),
            insert: (payload: Record<string, unknown>) => mocks.deliveryInsert(payload),
          };
        }
        if (table === "robots") {
          return { select: () => ({ order: mocks.robotRowsOrder }) };
        }
        throw new Error(`Unexpected Supabase table: ${table}`);
      },
      channel: () => channel,
      removeChannel: mocks.removeChannel,
    },
  };
});

import { AppProvider, useApp } from "../context/AppContext";

const deliveryInput: NewDeliveryInput = {
  sourceId: "loc-fcs",
  destinationId: "loc-data",
  itemName: "Authenticated profile test",
  category: "Documents",
  weightKg: 1,
  priority: "NORMAL",
  recipientName: "Data Center",
  recipientPhone: "+95 9 111 222 333",
};

function CreateDeliveryHarness() {
  const { createDelivery } = useApp();
  const [result, setResult] = useState("");

  const create = async () => {
    try {
      await createDelivery(deliveryInput);
      setResult("created");
    } catch (error) {
      setResult(error instanceof Error ? error.message : "failed");
    }
  };

  return (
    <>
      <button onClick={() => void create()}>Create cloud delivery</button>
      <output>{result}</output>
    </>
  );
}

describe("cloud delivery requester identity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getUser.mockResolvedValue({
      data: { user: { id: "user-123", email: "auth-metadata@miit.edu.mm" } },
      error: null,
    });
    mocks.profileRoleSingle.mockResolvedValue({ data: { role: "USER" }, error: null });
    mocks.requesterProfileMaybeSingle.mockResolvedValue({
      data: { full_name: "  Profile Person  ", email: "  profile@miit.edu.mm  " },
      error: null,
    });
    mocks.deliveryRowsOrder.mockResolvedValue({ data: [], error: null });
    mocks.robotRowsOrder.mockResolvedValue({ data: [], error: null });
    mocks.deliveryInsertSingle.mockResolvedValue({
      data: { id: "delivery-123", tracking_code: "MIIT-2001" },
      error: null,
    });
    mocks.deliveryInsert.mockImplementation(() => ({
      select: () => ({ single: mocks.deliveryInsertSingle }),
    }));
  });

  it("uses the authenticated profile snapshot instead of demo or auth metadata labels", async () => {
    const user = userEvent.setup();
    render(<AppProvider><CreateDeliveryHarness /></AppProvider>);

    await user.click(screen.getByRole("button", { name: "Create cloud delivery" }));
    expect(await screen.findByText("created")).toBeTruthy();

    await waitFor(() => expect(mocks.deliveryInsert).toHaveBeenCalledTimes(1));
    expect(mocks.deliveryInsert.mock.calls[0][0]).toMatchObject({
      requester_id: "user-123",
      requester_name: "Profile Person",
      requester_email: "profile@miit.edu.mm",
    });
  });

  it("does not insert a delivery when the authenticated profile is missing", async () => {
    mocks.requesterProfileMaybeSingle.mockResolvedValueOnce({ data: null, error: null });
    const user = userEvent.setup();
    render(<AppProvider><CreateDeliveryHarness /></AppProvider>);

    await user.click(screen.getByRole("button", { name: "Create cloud delivery" }));

    expect(await screen.findByText(/account profile is missing/i)).toBeTruthy();
    expect(mocks.deliveryInsert).not.toHaveBeenCalled();
  });
});
