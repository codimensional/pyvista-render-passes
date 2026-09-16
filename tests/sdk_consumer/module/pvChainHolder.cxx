#include "pvChainHolder.h"

#include <vtkObjectFactory.h>

vtkStandardNewMacro(pvChainHolder);

pvChainHolder::pvChainHolder() = default;

pvChainHolder::~pvChainHolder() = default;

void pvChainHolder::SetChain(pvRenderPassChain* chain)
{
  if (this->Chain != chain)
  {
    this->Chain = chain;
    this->Modified();
  }
}

pvRenderPassChain* pvChainHolder::GetChain()
{
  return this->Chain;
}

std::string pvChainHolder::DescribeChain()
{
  return this->Chain ? this->Chain->GetObjectDescription() : std::string();
}
