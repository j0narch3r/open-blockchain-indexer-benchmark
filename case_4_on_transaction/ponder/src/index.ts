/**
 * Case 4: Transaction Gas Usage Indexer
 * 
 * Optimized version using batched RPC calls with Promise.all
 * instead of sequential calls for each transaction.
 */
import { createPublicClient, http } from "viem";
import { ponder } from "ponder:registry";
import { gasSpent } from "../ponder.schema";
import { mainnet } from "viem/chains";

// Create Viem client with the RPC URL
const publicClient = createPublicClient({
  chain: mainnet,
  transport: http(process.env.PONDER_RPC_URL_1),
});

// Performance tracking
let totalProcessed = 0;
let totalBlocks = 0;
const startTime = Date.now();

// Batch size for concurrent RPC calls (adjust based on RPC rate limits)
const BATCH_SIZE = 50;

ponder.on("ethereum:block", async ({ event, context }) => {
  const block = event.block;
  totalBlocks++;

  try {
    // Get block with transactions - Ponder already provides this in event.block
    // But we need full transaction objects with gas info
    const blockWithTxs = await publicClient.getBlock({
      blockNumber: BigInt(block.number),
      includeTransactions: true,
    });

    const transactions = blockWithTxs.transactions;
    if (transactions.length === 0) return;

    // Process transactions in batches to avoid overwhelming the RPC
    for (let i = 0; i < transactions.length; i += BATCH_SIZE) {
      const batch = transactions.slice(i, i + BATCH_SIZE);

      // Fetch all receipts in parallel
      const receiptPromises = batch.map(tx =>
        publicClient.getTransactionReceipt({ hash: tx.hash }).catch(() => null)
      );

      const receipts = await Promise.all(receiptPromises);

      // Prepare batch insert data
      const insertData = [];

      for (let j = 0; j < batch.length; j++) {
        const tx = batch[j];
        const receipt = receipts[j];

        if (!receipt) continue;

        const gasPrice = tx.gasPrice || 0n;
        const effectiveGasPrice = receipt.effectiveGasPrice;
        const gasUsed = receipt.gasUsed;
        const priceForCalc = effectiveGasPrice || gasPrice;
        const gasValue = priceForCalc * gasUsed;

        insertData.push({
          id: tx.hash,
          from_address: tx.from,
          to_address: tx.to || "0x0000000000000000000000000000000000000000",
          gasValueString: gasValue.toString(),
          gasUsedString: gasUsed.toString(),
          gasPriceString: gasPrice.toString(),
          effectiveGasPriceString: effectiveGasPrice?.toString() || null,
          blockNumberString: block.number.toString(),
          transactionHash: tx.hash,
        });
      }

      // Batch insert all transactions
      if (insertData.length > 0) {
        for (const data of insertData) {
          await context.db.insert(gasSpent).values(data).onConflictDoNothing();
        }
        totalProcessed += insertData.length;
      }
    }

    // Log progress every 100 blocks
    if (totalBlocks % 100 === 0) {
      const elapsed = (Date.now() - startTime) / 1000;
      const txPerSec = totalProcessed / elapsed;
      console.log(
        `PROGRESS: Block ${block.number} | ` +
        `Blocks: ${totalBlocks} | ` +
        `Txs: ${totalProcessed} | ` +
        `Rate: ${txPerSec.toFixed(1)} tx/s | ` +
        `Elapsed: ${elapsed.toFixed(0)}s`
      );
    }
  } catch (error) {
    console.error(`Error processing block ${block.number}:`, error);
  }
});
