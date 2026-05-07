import { createPublicClient, http, parseAbi, getContract } from "viem";
import { experimental_createEffect, S } from "envio";
import { mainnet } from "viem/chains";
import { BigDecimal } from "generated";
import pLimit from "p-limit";

// Rate limiter - limit concurrent RPC calls to avoid rate limiting
// Adjust this number based on your RPC provider's limits
const rpcLimit = pLimit(10); // Max 3 concurrent RPC calls

// Define the ABI for the ERC20 balanceOf function
const erc20Abi = parseAbi([
  "function balanceOf(address owner) view returns (uint256)"
]);

// Get RPC URL from environment variable
const rpcUrl = process.env.ENVIO_RPC_URL;
if (!rpcUrl) {
  throw new Error("ENVIO_RPC_URL environment variable is required");
}

// Create a public client to interact with the blockchain
// Disable automatic batching - we'll use multicall explicitly
const client = createPublicClient({
  chain: mainnet,
  transport: http(rpcUrl),
});

// LBTC contract address
const LBTC_ADDRESS = "0x8236a87084f8B84306f72007F36F2618A5634494" as const;

// Get the contract instance for LBTC
const lbtcContract = getContract({
  abi: erc20Abi,
  address: LBTC_ADDRESS,
  client: client,
});

// Global variable to track RPC time
export let rpcTime = 0;

// Function to get the balance of a specific address at a specific block (legacy single call)
export const getBalance = experimental_createEffect({
  name: "getBalance",
  input: {
    address: S.string,
    blockNumber: S.optional(S.bigint),
  },
  output: S.bigDecimal,
}, async ({ input, context }) => {
  try {
    // If blockNumber is provided, use it to get balance at that specific block
    const options = input.blockNumber ? { blockNumber: input.blockNumber } : undefined;
    const startTime = performance.now();
    const balance = await lbtcContract.read.balanceOf([input.address as `0x${string}`], options);
    const endTime = performance.now();
    rpcTime += (endTime - startTime);
    return BigDecimal(balance.toString());
  } catch (error) {
    context.log.error(`Error getting balance for ${input.address}`, error as Error);
    // Return 0 on error to prevent processing failures
    return BigDecimal(0);
  }
});

/**
 * Batch get balances using viem's multicall
 * This combines multiple balanceOf calls into a single RPC request
 */
export const getBalancesBatch = experimental_createEffect({
  name: "getBalancesBatch",
  input: {
    addresses: S.array(S.string),
    blockNumber: S.bigint,
  },
  output: S.array(S.bigDecimal),
}, async ({ input, context }) => {
  const startTime = performance.now();

  try {
    if (input.addresses.length === 0) {
      return [];
    }

    // Use viem's multicall to batch all balanceOf calls into a single RPC request
    // Wrap with rate limiter to control concurrency
    const results = await rpcLimit(() => client.multicall({
      contracts: input.addresses.map(addr => ({
        address: LBTC_ADDRESS,
        abi: erc20Abi,
        functionName: 'balanceOf',
        args: [addr as `0x${string}`],
      })),
      blockNumber: input.blockNumber,
    }));

    rpcTime += performance.now() - startTime;

    // Map results to BigDecimal, handling errors gracefully
    return results.map((result, index) => {
      if (result.status === 'success' && result.result !== undefined) {
        return BigDecimal(result.result.toString());
      } else {
        context.log.warn(`Failed to get balance for ${input.addresses[index]}: ${result.error?.message || 'Unknown error'}`);
        return BigDecimal(0);
      }
    });
  } catch (error) {
    context.log.error(`Multicall error for ${input.addresses.length} addresses`, error as Error);
    rpcTime += performance.now() - startTime;
    // Return array of zeros on error
    return input.addresses.map(() => BigDecimal(0));
  }
});