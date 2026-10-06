import assert from 'node:assert/strict'
import test from 'node:test'
import { withNetworkRetry } from '../src/lib/networkRetry.ts'

class NetworkError extends Error {}
class ApplicationError extends Error {}
const isNetwork = (e: unknown) => e instanceof NetworkError
const fastDelays = [1, 1, 1]

test('a dropped connection is retried until it succeeds', async () => {
  let calls = 0
  const result = await withNetworkRetry(async () => {
    calls++
    if (calls < 3) throw new NetworkError('dropped')
    return 'ok'
  }, isNetwork, undefined, fastDelays)
  assert.equal(result, 'ok')
  assert.equal(calls, 3)
})

test('gives up once the retry budget is exhausted, surfacing the last failure', async () => {
  let calls = 0
  await assert.rejects(
    withNetworkRetry(async () => { calls++; throw new NetworkError('still dropped') }, isNetwork, undefined, fastDelays),
    NetworkError,
  )
  assert.equal(calls, 1 + fastDelays.length)
})

test('never retries a failure the server actually sent back', async () => {
  let calls = 0
  await assert.rejects(
    withNetworkRetry(async () => { calls++; throw new ApplicationError('rejected by the server') }, isNetwork, undefined, fastDelays),
    ApplicationError,
  )
  assert.equal(calls, 1)
})

test('onRetry fires once per retry, with the 1-based attempt number, before success', async () => {
  const attempts: number[] = []
  let calls = 0
  await withNetworkRetry(async () => {
    calls++
    if (calls < 3) throw new NetworkError('dropped')
    return 'ok'
  }, isNetwork, attempt => attempts.push(attempt), fastDelays)
  assert.deepEqual(attempts, [1, 2])
})
