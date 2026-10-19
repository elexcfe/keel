import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import commandTimeout from '../src/command_timeout.mjs';

let handler;
commandTimeout({on: (event, fn) => { assert.equal(event, 'tool_call'); handler = fn; }});
for (const [requested, expected] of [[undefined,600],[0,600],[-1,600],[Infinity,600],[300,300],[99999,600],[0.2,0.2]]) {
  const event = {toolName:'bash',input:{command:'sleep 30',timeout:requested}};
  handler(event); assert.equal(event.input.timeout,expected);
}
// 不解析 shell：直接调用、绝对路径、组合命令采用同一默认值。
for (const command of ['bash run_scenario.sh', 'bash "/tmp/task space/run_scenario.sh"', 'cd repo && bash ../run_scenario.sh']) {
  const event = {toolName:'bash',input:{command}};
  handler(event);
  assert.equal(event.input.timeout,600);
  assert.ok(event.input.timeout > 300 + 30, '默认 bash 必须容纳场景和清理');
}
const other = {toolName:'read',input:{path:'a'}};
handler(other); assert.equal(other.input.timeout,undefined);

// 指向本机已安装 Pi；不用模型、不联网。未提供时只运行策略测试。
if (process.env.PI_PACKAGE_PATH) {
  const base=resolve(process.env.PI_PACKAGE_PATH);
  const moduleAt=(p)=>import(pathToFileURL(join(base,p)).href);
  const {loadExtensions}=await moduleAt('dist/core/extensions/loader.js');
  const {ExtensionRunner}=await moduleAt('dist/core/extensions/runner.js');
  const {createBashTool}=await moduleAt('dist/core/tools/bash.js');
  const root=mkdtempSync(join(tmpdir(),'keel-pi-timeout-'));
  try {
    const loaded=await loadExtensions([resolve('script_tool/make_bug/src/command_timeout.mjs')],root);
    assert.deepEqual(loaded.errors,[]); assert.equal(loaded.extensions.length,1);
    const runner=new ExtensionRunner(loaded.extensions,loaded.runtime,root,{},{});
    const tool=createBashTool(root);
    const event={type:'tool_call',toolName:'bash',toolCallId:'timeout-test',input:{command:'sleep 30 & echo $! > child.pid; wait',timeout:0.2}};
    await runner.emitToolCall(event);
    const start=Date.now();
    await assert.rejects(tool.execute(event.toolCallId,event.input),/timed out|timeout/i);
    assert.ok(Date.now()-start<3000,'超时应及时返回');
    const pid=Number(readFileSync(join(root,'child.pid'),'utf8'));
    let alive=false;
    try {const stat=readFileSync(`/proc/${pid}/stat`,'utf8');alive=!stat.slice(stat.lastIndexOf(')')+2).startsWith('Z');} catch {}
    assert.equal(alive,false,'超时后子进程不应继续执行');
    const next={type:'tool_call',toolName:'bash',toolCallId:'after-timeout',input:{command:'printf recovered'}};
    await runner.emitToolCall(next);assert.equal(next.input.timeout,600);
    const result=await tool.execute(next.toolCallId,next.input);
    assert.match(JSON.stringify(result),/recovered/);
    process.env.MAKE_BUG_DEADLINE_MS=String(Date.now()+200);
    process.env.MAKE_BUG_WRAP_UP_MS='1000';
    const blocked=await runner.emitToolCall({type:'tool_call',toolName:'bash',toolCallId:'wrap-up',input:{command:'true'}});
    assert.equal(blocked.block,true);
    delete process.env.MAKE_BUG_DEADLINE_MS; delete process.env.MAKE_BUG_WRAP_UP_MS;
    console.log('PASS: actual Pi extension loading, timeout, child cleanup, subsequent command');
  } finally {rmSync(root,{recursive:true,force:true});}
}
console.log('PASS: default timeout, explicit timeout, upper cap, invalid input, unrelated tools');

process.env.MAKE_BUG_DEADLINE_MS = String(Date.now() + 3000);
process.env.MAKE_BUG_WRAP_UP_MS = '1000';
const bounded = {toolName:'bash', input:{command:'sleep 30', timeout:30}};
handler(bounded);
assert.ok(bounded.input.timeout > 0 && bounded.input.timeout <= 2);
process.env.MAKE_BUG_DEADLINE_MS = String(Date.now() + 500);
assert.equal(handler({toolName:'bash', input:{command:'true'}}).block, true);
assert.equal(handler({toolName:'write', input:{path:'result/REPRO.md',content:'partial'}}), undefined);
delete process.env.MAKE_BUG_DEADLINE_MS;
delete process.env.MAKE_BUG_WRAP_UP_MS;
console.log('PASS: budget boundary blocks bash and leaves report writing available');
