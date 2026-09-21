import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInWorker } from "./execution-worker.mjs";

test("worker completes before returning", async () => {
  const dir = mkdtempSync(join(tmpdir(), "qa-worker-"));
  try {
    const file = join(dir, "worker.cjs");
    writeFileSync(file, 'process.once("message", () => process.send({type:"result", result:{verdict:"pass"}}, () => process.exit(0)))');
    assert.equal((await runInWorker({file,item:{},heartbeat:async()=>({alive:true}),pollMs:20})).verdict,"pass");
  } finally { rmSync(dir,{recursive:true,force:true}); }
});
test("cancel before startup never executes", async () => {
  const result = await runInWorker({file:"must-not-spawn", item:{}, heartbeat:async()=>({alive:true,cancel_requested:true})});
  assert.equal(result.verdict,"fail");
});
for (const mode of ["cancel", "timeout", "offline"]) {
  test(`${mode} kills a stuck worker AND its tool child before returning`, async () => {
    const dir = mkdtempSync(join(tmpdir(), "qa-cancel-"));
    try {
      const file = join(dir, "worker.cjs"), marker = join(dir,"ticks");
      const tool = `const fs=require('fs');setInterval(()=>fs.appendFileSync(${JSON.stringify(marker)},'x'),20)`;
      writeFileSync(file, `process.once('message',()=>{require('child_process').spawn(process.execPath,['-e',${JSON.stringify(tool)}],{stdio:'ignore'});setInterval(()=>{},1000)});`);
      let calls=0;
      const result = await runInWorker({file,item:{},pollMs:40,timeoutMs:mode==="timeout"?1500:5000,heartbeat:async()=>{
        calls++;
        if (calls>1 && existsSync(marker)) {
          if(mode==="offline") throw Error("offline");
          if(mode==="cancel") return {alive:true,cancel_requested:true};
        }
        return {alive:true};
      }});
      assert.equal(result.verdict,"fail");
      assert.ok(existsSync(marker),"tool must actually have started");
      const before=readFileSync(marker,"utf8");
      await new Promise(r=>setTimeout(r,200));
      assert.equal(readFileSync(marker,"utf8"),before,"orphan tool must not continue operating");
    } finally { rmSync(dir,{recursive:true,force:true}); }
  });
}
