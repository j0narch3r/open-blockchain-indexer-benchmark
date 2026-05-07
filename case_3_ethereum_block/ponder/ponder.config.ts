import { createConfig } from "ponder";

export default createConfig({
  chains: {
    mainnet: {
      id: 1,
      rpc: process.env.PONDER_RPC_URL_1,
    },
  },
  blocks: {
    EveryBlock: {
      chain: "mainnet",
      startBlock: 0,
      endBlock: 100000,
    }
  },
});
