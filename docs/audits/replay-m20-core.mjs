// Local audit replay only: imports the real pinned Core resolver, without network or database access.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { registerHooks } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

assert.ok(process.argv[2], 'Pass the local MarkOrbit checkout path.');
const root = resolve(process.argv[2]);
const baseline = '86715f76d4a5b33839f431e4996a8a00b21c7918';
assert.equal(execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(), baseline);
assert.equal(execFileSync('git', ['-C', root, 'diff', 'HEAD', '--',
  'services/core/src/workspace-commercial.ts', 'packages/contracts/src/workspace-commercial.ts'],
  { encoding: 'utf8' }), '', 'The replayed Core and contract files must match the pinned baseline.');
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '@markorbit/contracts/workspace-commercial') {
      return nextResolve(pathToFileURL(`${root}/packages/contracts/src/workspace-commercial.ts`).href, context);
    }
    return nextResolve(specifier, context);
  }
});
const { InMemoryWorkspaceCommercialRepositoryV1, WorkspaceCommercialServiceV1 } =
  await import(pathToFileURL(`${root}/services/core/src/workspace-commercial.ts`).href);
const repository = new InMemoryWorkspaceCommercialRepositoryV1();
const service = new WorkspaceCommercialServiceV1(repository, async () => undefined);
const subject = { scope: 'WORKSPACE', workspaceId: 'audit-workspace-a' };
const key = 'audit-only.us.applicant.search.portfolio';
const past = '2026-10-01T00:00:00.000Z';
const now = '2026-10-09T00:00:00.000Z';
const grant = {
  schemaVersion: 1, grantId: 'audit-grant', version: 1, subject,
  entitlement: { key, subjectScope: 'WORKSPACE', value: { kind: 'BOOLEAN', enabled: true } },
  status: 'ACTIVE', sourceType: 'MANUAL', sourceRef: 'AUDIT_ONLY_NOT_A_COMMERCIAL_SCOPE',
  effectiveFrom: '2026-09-01T00:00:00.000Z', recordedAt: '2026-09-01T00:00:00.000Z'
};
await repository.appendGrant(grant);
let checks = 0;
const active = await service.resolveEntitlement(subject, key, now);
assert.equal(active.value.enabled, true);
assert.deepEqual(active.contributingGrantRefs, [{ grantId: grant.grantId, version: 1 }]);
checks++;
const denied = (subjectArg, keyArg, at) => assert.rejects(
  service.resolveEntitlement(subjectArg, keyArg, at),
  error => error.code === 'NO_APPLICABLE_ENTITLEMENT'
);
await denied({ scope: 'WORKSPACE', workspaceId: 'audit-workspace-b' }, key, now);
checks++;
await denied(subject, 'audit-only.us.applicant.opportunity.portfolio', now);
checks++;
await repository.appendGrant({ ...grant, version: 2, status: 'REVOKED', recordedAt: '2026-10-05T00:00:00.000Z' });
await denied(subject, key, now);
checks++;
const historical = await service.resolveEntitlement(subject, key, past);
assert.equal(historical.value.enabled, true);
assert.equal(historical.resolvedAt, past);
checks++;
await repository.appendGrant({ ...grant, grantId: 'audit-expiring-grant', effectiveTo: now });
await denied(subject, key, now);
checks++;
await repository.appendGrant({
  ...grant, grantId: 'audit-disabled-grant',
  entitlement: { ...grant.entitlement, value: { kind: 'BOOLEAN', enabled: false } }
});
assert.equal((await service.resolveEntitlement(subject, key, now)).value.enabled, false);
checks++;
console.log(JSON.stringify({ baseline, checksPassed: checks, fixtureOnly: true,
  runtimeDataUseEnforcementVerified: false }, null, 2));
