import { createConfig } from "ponder";

import { UniswapV2Router02ABI } from "./abis/UniswapV2Router02";

export default createConfig({
  chains: {
    mainnet: {
      id: 1,
      rpc: process.env.PONDER_RPC_URL_1,
    },
  },
  contracts: {
    UniswapV2Router02: {
      chain: "mainnet",
      abi: UniswapV2Router02ABI,
      address: "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
      startBlock: 22200000,
      endBlock: 22290000,
      includeCallTraces: true,
    }
  },
});
