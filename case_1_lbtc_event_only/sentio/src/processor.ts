import { LBTCContext, LBTCProcessor, TransferEvent } from './types/eth/lbtc.js'

import { LBTC_PROXY, } from "./constant.js"
import { Transfer } from './schema/schema.js'

const transferEventHandler = async function (event: TransferEvent, ctx: LBTCContext) {
  const transfer = new Transfer({
    id: `${ctx.chainId}_${event.blockNumber}_${event.index}`,
    from: event.args.from,
    to: event.args.to,
    value: event.args.value,
    blockNumber: BigInt(event.blockNumber),
    transactionHash: Buffer.from(event.transactionHash.slice(2), 'hex') as unknown as Uint8Array,
    indexedAt: BigInt(Date.now()),
  })

  await ctx.store.upsert(transfer)
  // ctx.eventLogger.emit("Transfer", {
  //   id: `${ctx.chainId}_${event.blockNumber}_${event.index}`,
  //   from: event.args.from,
  //   to: event.args.to,
  //   value: event.args.value,
  //   blockNumber: BigInt(event.blockNumber),
  //   transactionHash: Buffer.from(event.transactionHash.slice(2), 'hex') as unknown as Uint8Array,
  //   indexedAt: BigInt(Date.now()),
  // })
}


LBTCProcessor.bind({
  address: LBTC_PROXY,
  startBlock: 1, endBlock: 22200000
})
  .onEventTransfer(transferEventHandler) // if filter by mint LBTC Processor.filters.Transfer(0x0, null)
