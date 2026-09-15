# Codex browser-control validation update

Hi Ravid,

I reran the validation on 14 August 2026 using the Codex Chrome-control workflow. It fails before Codex can select or inspect Chrome, so I cannot perform the requested read-only validation of the Workato shadow-mode onboarding recipe.

The diagnostic produced by the validation is:

```text
node_repl kernel exited unexpectedly

node_repl diagnostics: {"kernel_pid":27780,"kernel_status":"exited(code=1)","kernel_stderr_tail":"windows sandbox failed: CryptUnprotectData failed: 2148073483","reason":"stdout_eof","stream_error":null}
```

I have attached `IT_Evidence_Codex_Chrome_Control_20260814.svg`, a rendered evidence capture of this raw diagnostic. It is not a live Settings screenshot: the DPAPI/sandbox failure prevents Codex from accessing the native desktop or Chrome in order to take one.

Could you please advise whether the Windows user profile DPAPI/protected key-store requires repair, or whether there is a Codex desktop recovery procedure for this condition?

Thanks,
Milton
