/**
 * @class   pvChainHolder
 * @brief   Holds a pvRenderPassChain; its public methods take and return one.
 *
 * VTK's wrappers only wrap a method taking a class they can resolve through a
 * hierarchy file, so SetChain and GetChain exist in Python only when the SDK's
 * hierarchy file reached this build. DescribeChain calls a std::string-returning
 * VTK method, so it resolves only when this build uses VTK's libstdc++ ABI.
 */
#ifndef pvChainHolder_h
#define pvChainHolder_h

#include "SdkConsumerModule.h"
#include "pvRenderPassChain.h"

#include <vtkObject.h>
#include <vtkSmartPointer.h>

#include <string>

class SDKCONSUMER_EXPORT pvChainHolder : public vtkObject
{
public:
  static pvChainHolder* New();
  vtkTypeMacro(pvChainHolder, vtkObject);

  void SetChain(pvRenderPassChain* chain);
  pvRenderPassChain* GetChain();

  /**
   * The held chain's vtkObjectBase::GetObjectDescription, or an empty string.
   */
  std::string DescribeChain();

protected:
  pvChainHolder();
  ~pvChainHolder() override;

private:
  pvChainHolder(const pvChainHolder&) = delete;
  void operator=(const pvChainHolder&) = delete;

  vtkSmartPointer<pvRenderPassChain> Chain;
};

#endif
