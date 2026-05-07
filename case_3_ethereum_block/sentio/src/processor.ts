import { Block } from './schema/schema.js'
import { GlobalProcessor } from '@sentio/sdk/eth';

GlobalProcessor.bind({
    startBlock: 0, endBlock: 100000
})
    .onBlockInterval(async (block, ctx) => {
        const blockEntity = new Block({
            id: `${ctx.chainId}_${block.number}`,
            number: BigInt(block.number),
            hash: block.hash || '',
            parentHash: block.parentHash || '',
            timestampValue: BigInt(block.timestamp || 0),
            indexAt: BigInt(Date.now())
        })
        await ctx.store.upsert(blockEntity);
    }, 1, 1)
