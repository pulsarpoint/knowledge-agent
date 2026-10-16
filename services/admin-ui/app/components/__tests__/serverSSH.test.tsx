import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ServerSSHForm from "~/features/servers/ServerSSHForm";
import * as api from "~/features/servers/operations";
vi.mock("~/features/servers/operations");
const server: api.SSHServer = {
  id: "server-1",
  name: "Staging",
  hostname: "staging.internal",
  port: 22,
  username: "operator",
  revision: 2,
  ssh_allowed: true,
  enabled: true,
  allow_sudo: false,
  known_hosts: "staging.internal ssh-ed25519 test",
  has_private_key: true,
  last_tested_at: null,
  last_test_error: null,
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getSSH).mockResolvedValue(server);
});
describe("SSH settings", () => {
  it.each(["closed", "empty", "whitespace", "cancelled"])(
    "keeps the saved key with replacement %s and allows testing with unsaved permissions",
    async (mode) => {
      vi.mocked(api.saveSSH).mockResolvedValue({ ...server, revision: 3, allow_sudo: true });
      render(<ServerSSHForm id={server.id} onClose={vi.fn()} />);
      expect(await screen.findByText("Private key saved")).toBeInTheDocument();
      expect(screen.queryByRole("textbox", { name: "SSH private key" })).not.toBeInTheDocument();
      if (mode !== "closed") {
        fireEvent.click(screen.getByRole("button", { name: "Replace key" }));
        const input = screen.getByLabelText("SSH private key");
        expect(input).toHaveValue("");
        if (mode === "whitespace") fireEvent.change(input, { target: { value: "  \n  " } });
        if (mode === "cancelled") {
          fireEvent.change(input, { target: { value: "discarded-replacement" } });
          fireEvent.click(screen.getByRole("button", { name: "Cancel replacement" }));
          expect(screen.queryByText(/You have unsaved changes/)).not.toBeInTheDocument();
        }
      }
      fireEvent.click(screen.getByRole("switch", { name: "Allow sudo to root" }));
      expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
      await waitFor(() =>
        expect(api.saveSSH).toHaveBeenCalledWith(server.id, {
          revision: 2,
          username: "operator",
          port: 22,
          allow_sudo: true,
          known_hosts: server.known_hosts,
        }),
      );
      expect(await screen.findByRole("status")).toHaveTextContent("saved");
    },
  );
  it("submits a replacement key only when entered and clears the input after saving", async () => {
    vi.mocked(api.saveSSH).mockResolvedValue({ ...server, revision: 3 });
    render(<ServerSSHForm id={server.id} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Replace key" }));
    fireEvent.change(screen.getByLabelText("SSH private key"), { target: { value: "replacement-test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() =>
      expect(api.saveSSH).toHaveBeenCalledWith(
        server.id,
        expect.objectContaining({ private_key: "replacement-test-key" }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "SSH private key" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Replace key" })).toBeInTheDocument();
  });
  it("tests an unsaved key without trusted hosts, then lets the user add and save the discovered key", async () => {
    vi.mocked(api.getSSH).mockResolvedValue({
      ...server,
      username: null,
      has_private_key: false,
      known_hosts: "",
      enabled: false,
    });
    const hostKey = {
      public_key: "ssh-ed25519 public-test-key",
      known_hosts_line: "staging.internal ssh-ed25519 public-test-key",
      fingerprint: "SHA256:test-fingerprint",
      algorithm: "ssh-ed25519",
      verified: false,
    };
    vi.mocked(api.testSSH).mockResolvedValue({
      ok: true,
      message: "SSH connected. Host verification is off.",
      host_key: hostKey,
    });
    vi.mocked(api.saveSSH).mockResolvedValue({ ...server, revision: 3, known_hosts: hostKey.known_hosts_line });
    render(<ServerSSHForm id={server.id} onClose={vi.fn()} />);
    const privateKey = await screen.findByLabelText("SSH private key");
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Default SSH user"), { target: { value: "operator" } });
    fireEvent.change(privateKey, { target: { value: "draft-private-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("SHA256:test-fingerprint")).toBeInTheDocument();
    expect(api.testSSH).toHaveBeenCalledWith(
      server.id,
      expect.objectContaining({ username: "operator", private_key: "draft-private-key", known_hosts: "" }),
    );
    expect(api.saveSSH).not.toHaveBeenCalled();
    expect(privateKey).toHaveValue("draft-private-key");
    fireEvent.click(screen.getByRole("button", { name: "Add to trusted hosts" }));
    expect(screen.getByLabelText("Trusted host keys (optional)")).toHaveValue(hostKey.known_hosts_line);
    expect(screen.queryByText("SHA256:test-fingerprint")).not.toBeInTheDocument();
    expect(api.saveSSH).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() =>
      expect(api.saveSSH).toHaveBeenCalledWith(
        server.id,
        expect.objectContaining({ private_key: "draft-private-key", known_hosts: hostKey.known_hosts_line }),
      ),
    );
  });
  it("clears stale discovery after connection changes and does not offer trust after failure", async () => {
    vi.mocked(api.testSSH)
      .mockResolvedValueOnce({
        ok: true,
        message: "Connected",
        host_key: {
          public_key: "public",
          fingerprint: "SHA256:old",
          algorithm: "ssh-ed25519",
          known_hosts_line: "staging.internal ssh-ed25519 observed",
          verified: true,
        },
      })
      .mockResolvedValueOnce({ ok: false, message: "Host key verification failed.", host_key: null });
    render(<ServerSSHForm id={server.id} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("SHA256:old")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("SSH port"), { target: { value: "2222" } });
    expect(screen.queryByRole("button", { name: "Add to trusted hosts" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Host key verification failed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add to trusted hosts" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Remove saved key" }));
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
  });
});
